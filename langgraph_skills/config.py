"""全局配置（单一入口）：环境变量 + 三层 JSON 配置文件。

对应 PROCESS.md 设计基线：
  - 密钥、模型、base_url 等引擎参数统一在此
  - 配置来源（优先级从低到高）：内置默认 < 全局配置 < 项目配置 < 环境变量
  - 密钥不写入配置文件：用 {file:path}（读文件）或 {env:VAR}（读环境变量）引用
  - 配置格式 JSON，对齐 opencode 的 provider/model 惯例

配置位置：
  - 全局：~/.config/langgraph_skills/config.json
  - 项目：<项目根>/lgskills.json
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 环境变量名（统一前缀 LGSKILLS_）
ENV_MODEL = "LGSKILLS_MODEL"
ENV_BASE_URL = "LGSKILLS_BASE_URL"
ENV_TEMPERATURE = "LGSKILLS_TEMPERATURE"
ENV_STRICT = "LGSKILLS_STRICT"

# 默认值
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_LOOPS = 10
DEFAULT_PROVIDER = "deepseek"

# 配置文件名
GLOBAL_CONFIG_DIR = "langgraph_skills"
GLOBAL_CONFIG_NAME = "config.json"
TRIGGERS_CONFIG_NAME = "triggers.json"
PROJECT_CONFIG_NAME = "lgskills.json"


# ---------------------------------------------------------------------------
# 变量展开：{file:path} / {env:VAR}
# ---------------------------------------------------------------------------


def expand_value(value: Any) -> Any:
    """展开字符串中的 {file:path} / {env:VAR} 引用。

    仅对顶层/嵌套的字符串值生效；非字符串原样返回。
    """
    if not isinstance(value, str):
        return value
    if value.startswith("{file:") and value.endswith("}"):
        path = value[len("{file:") : -1]
        expanded = os.path.expanduser(path)
        try:
            with open(expanded, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"Warning: Failed to read {expanded}: {e}", file=sys.stderr)
            return None
    if value.startswith("{env:") and value.endswith("}"):
        var = value[len("{env:") : -1]
        return os.environ.get(var)
    return value


def expand_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """递归展开配置中的所有 {file:}/{env:} 引用。"""
    if isinstance(data, dict):
        return {k: expand_config(v) for k, v in data.items()}
    if isinstance(data, list):
        return [expand_config(item) for item in data]
    return expand_value(data)


# ---------------------------------------------------------------------------
# 配置加载：三层合并
# ---------------------------------------------------------------------------


def _global_config_path() -> Path:
    return Path.home() / ".config" / GLOBAL_CONFIG_DIR / GLOBAL_CONFIG_NAME


def _project_config_path() -> Optional[Path]:
    """从当前目录向上查找项目配置 lgskills.json。"""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / PROJECT_CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Failed to load config {path}: {e}", file=sys.stderr)
        return {}


def load_config(
    env: Optional[Dict[str, str]] = None,
    global_path: Optional[Path] = None,
    project_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """加载三层配置（默认 < 全局 < 项目），并展开变量引用。

    参数可注入以便测试；缺省时使用真实环境。
    """
    merged: Dict[str, Any] = {}
    for path in [
        global_path or _global_config_path(),
        project_path or _project_config_path(),
    ]:
        if path is not None and path.exists():
            data = _load_json(path)
            merged = _deep_merge(merged, data)
    return expand_config(merged)


def load_triggers(
    global_path: Optional[Path] = None,
    project_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """加载 triggers.json（全局 + 项目），返回 triggers 列表（拼接，非覆盖）。

    triggers.json 是独立文件（PROCESS.md §7.6 决策 #0），与 config.json 分开。
    全局与项目的 triggers 列表**拼接**：两者都生效，项目条目在后。
    """
    triggers: List[Dict[str, Any]] = []
    for path in [
        global_path or _global_config_path().with_name(TRIGGERS_CONFIG_NAME),
        project_path or _triggers_project_path(),
    ]:
        if path is not None and path.exists():
            data = _load_json(path)
            raw = data.get("triggers", []) if isinstance(data, dict) else []
            if isinstance(raw, list):
                triggers.extend(item for item in raw if isinstance(item, dict))
    # 逐条展开变量引用（expand_config 只接受 dict）
    return [expand_config(item) for item in triggers]


def _triggers_project_path() -> Optional[Path]:
    """项目目录下查找 triggers.json（与 lgskills.json 同级）。"""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / TRIGGERS_CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并两个字典（override 覆盖 base；嵌套 dict 递归合并）。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Provider / Model 解析
# ---------------------------------------------------------------------------


def parse_model_ref(model_ref: str) -> tuple[str, str]:
    """解析 "provider/model" 引用为 (provider, model)。

    无斜杠时默认 provider 为 deepseek。
    """
    if "/" in model_ref:
        provider, _, model = model_ref.partition("/")
        return provider, model
    return DEFAULT_PROVIDER, model_ref


def resolve_provider_options(config: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """从配置中取 provider 的 options（apiKey/baseURL 等）。"""
    providers = config.get("provider", {})
    provider_cfg = providers.get(provider, {}) if isinstance(providers, dict) else {}
    options = provider_cfg.get("options", {}) if isinstance(provider_cfg, dict) else {}
    return options if isinstance(options, dict) else {}


def list_providers(config: Dict[str, Any]) -> Dict[str, list[str]]:
    """返回 {provider: [model_name, ...]} 映射（含内置默认）。"""
    result: Dict[str, list[str]] = {}
    providers = config.get("provider", {})
    if isinstance(providers, dict):
        for name, cfg in providers.items():
            if isinstance(cfg, dict):
                models = cfg.get("models", {})
                result[name] = list(models.keys()) if isinstance(models, dict) else []
    # 确保默认 provider 存在
    result.setdefault(DEFAULT_PROVIDER, [DEFAULT_MODEL])
    return result


# ---------------------------------------------------------------------------
# 运行时设置
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """引擎运行时配置（已解析、已展开变量）。"""

    api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_BASE_URL
    temperature: float = DEFAULT_TEMPERATURE
    strict: bool = False  # True 时未知 section 等容错从 warning 升级为 error
    config: Dict[str, Any] = field(default_factory=dict)  # 合并后的原始配置

    @classmethod
    def load(
        cls,
        env: Optional[Dict[str, str]] = None,
        global_path: Optional[Path] = None,
        project_path: Optional[Path] = None,
    ) -> "Settings":
        """从环境变量 + 三层 JSON 配置加载设置。

        global_path / project_path 可注入以便测试；缺省使用真实路径。
        """
        merged = load_config(env=env, global_path=global_path, project_path=project_path)

        # 默认模型：配置 model 字段或环境变量
        model_ref = os.environ.get(ENV_MODEL) or merged.get("model") or DEFAULT_MODEL
        provider, model = parse_model_ref(model_ref)

        # provider options（环境变量优先于配置文件）
        options = resolve_provider_options(merged, provider)
        api_key = options.get("apiKey") or get_deepseek_key()
        base_url = (
            os.environ.get(ENV_BASE_URL)
            or options.get("baseURL")
            or DEFAULT_BASE_URL
        )

        try:
            temperature = float(
                os.environ.get(ENV_TEMPERATURE)
                or options.get("temperature")
                or merged.get("temperature")
                or DEFAULT_TEMPERATURE
            )
        except (TypeError, ValueError):
            temperature = DEFAULT_TEMPERATURE

        strict = os.environ.get(ENV_STRICT, "").lower() in ("1", "true", "yes") or merged.get("strict") is True

        return cls(
            api_key=api_key,
            model=model,
            provider=provider,
            base_url=base_url,
            temperature=temperature,
            strict=strict,
            config=merged,
        )

    @classmethod
    def from_env(cls) -> "Settings":
        """兼容别名：等价于 load()。"""
        return cls.load()


# ---------------------------------------------------------------------------
# 密钥解析（兼容保留）
# ---------------------------------------------------------------------------


def get_deepseek_key() -> Optional[str]:
    """Resolve the API key from, in priority order:
    1. The `DEEPSEEK_API_KEY` environment variable.
    2. A `.env` file in the current working directory (via python-dotenv).
    3. A key file whose path is set via the `DEEPSEEK_API_KEY_FILE`
       environment variable.

    Returns ``None`` when no key is available.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if api_key:
        return api_key.strip()

    key_file = os.environ.get("DEEPSEEK_API_KEY_FILE")
    if key_file:
        key_path = os.path.expanduser(key_file)
        if os.path.exists(key_path):
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                print(f"Warning: Failed to read {key_path}: {e}", file=sys.stderr)
    return None
