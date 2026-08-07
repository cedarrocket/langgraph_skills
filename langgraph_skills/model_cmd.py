"""`lgskills model` 子命令实现：模型管理 + opencode 配置导入。

命令：
  model list                  列出可用 provider/models
  model set <provider/model>  设置默认模型（写全局配置）
  model config                显示当前生效配置
  model import-opencode       从 opencode 全局配置导入 provider
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph_skills.config import (
    GLOBAL_CONFIG_DIR,
    GLOBAL_CONFIG_NAME,
    PROJECT_CONFIG_NAME,
    list_providers,
    load_config,
)

OPENCODE_GLOBAL = Path.home() / ".config" / "opencode" / "opencode.json"


def _global_config_path() -> Path:
    return Path.home() / ".config" / GLOBAL_CONFIG_DIR / GLOBAL_CONFIG_NAME


def _load_global_config() -> Dict[str, Any]:
    path = _global_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Failed to load {path}: {e}", file=sys.stderr)
            return {}
    return {}


def _save_global_config(config: Dict[str, Any]) -> None:
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Saved to {path}", file=sys.stderr)


def cmd_list() -> None:
    merged = load_config()
    providers = list_providers(merged)
    print("Available providers and models:")
    for provider, models in providers.items():
        print(f"  {provider}: {', '.join(models) if models else '(no models declared)'}")


def cmd_set(model_ref: str) -> None:
    config = _load_global_config()
    config["model"] = model_ref
    _save_global_config(config)
    print(f"Default model set to '{model_ref}'.")


def cmd_config() -> None:
    from langgraph_skills.config import Settings

    settings = Settings.load()
    print(f"Provider: {settings.provider}")
    print(f"Model:    {settings.model}")
    print(f"Base URL: {settings.base_url}")
    print(f"API key:  {'*** set ***' if settings.api_key else '(not set)'}")
    print(f"Temperature: {settings.temperature}")
    print(f"Strict:   {settings.strict}")
    print(f"Global config: {_global_config_path()}")
    proj = _find_project_config()
    print(f"Project config: {proj or '(none found)'}")


def _find_project_config() -> Optional[Path]:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / PROJECT_CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# opencode 导入
# ---------------------------------------------------------------------------


def cmd_import_opencode() -> None:
    """从 opencode 全局配置导入 provider 设置。

    读取 ~/.config/opencode/opencode.json，将其中已配置的 provider
    （含 models / options.apiKey / options.baseURL）合并到我们的全局配置。
    """
    if not OPENCODE_GLOBAL.exists():
        print(
            f"Warning: opencode global config not found at {OPENCODE_GLOBAL}. "
            "Nothing imported.",
            file=sys.stderr,
        )
        return

    try:
        with open(OPENCODE_GLOBAL, "r", encoding="utf-8") as f:
            opencode_cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Failed to parse {OPENCODE_GLOBAL}: {e}", file=sys.stderr)
        return

    opencode_providers = opencode_cfg.get("provider", {})
    if not isinstance(opencode_providers, dict) or not opencode_providers:
        print("No providers found in opencode config.", file=sys.stderr)
        return

    our_config = _load_global_config()
    our_providers = our_config.setdefault("provider", {})
    if not isinstance(our_providers, dict):
        our_providers = {}
        our_config["provider"] = our_providers

    imported = []
    for name, provider_cfg in opencode_providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        options = provider_cfg.get("options", {})
        models = provider_cfg.get("models", {})
        if not isinstance(options, dict):
            options = {}
        if not isinstance(models, dict):
            models = {}

        # 构造我们的 provider 条目（options 中的 apiKey/baseURL 保留引用写法）
        our_entry: Dict[str, Any] = {}
        if models:
            our_entry["models"] = models
        if options:
            our_options: Dict[str, Any] = {}
            for opt_key in ("apiKey", "baseURL", "timeout"):
                if opt_key in options:
                    our_options[opt_key] = options[opt_key]
            if our_options:
                our_entry["options"] = our_options

        our_providers[name] = our_entry
        imported.append(name)

    if imported:
        _save_global_config(our_config)
        print(f"Imported providers from opencode: {', '.join(imported)}")
    else:
        print("No importable provider settings found in opencode config.", file=sys.stderr)
