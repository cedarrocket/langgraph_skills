import json

import pytest

from langgraph_skills.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    Settings,
    expand_config,
    expand_value,
    get_deepseek_key,
    load_config,
    parse_model_ref,
)


def test_settings_defaults():
    s = Settings.from_env()
    assert s.model == DEFAULT_MODEL
    assert s.base_url == DEFAULT_BASE_URL
    assert s.temperature == 0.0
    assert s.strict is False


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("LGSKILLS_MODEL", "my-model")
    monkeypatch.setenv("LGSKILLS_BASE_URL", "https://custom.example.com/v1")
    monkeypatch.setenv("LGSKILLS_TEMPERATURE", "0.7")
    monkeypatch.setenv("LGSKILLS_STRICT", "true")

    s = Settings.from_env()
    assert s.model == "my-model"
    assert s.base_url == "https://custom.example.com/v1"
    assert s.temperature == 0.7
    assert s.strict is True


@pytest.mark.parametrize("val,expected", [("1", True), ("true", True), ("yes", True), ("0", False), ("false", False), ("", False)])
def test_settings_strict_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("LGSKILLS_STRICT", val)
    assert Settings.from_env().strict is expected


def test_get_deepseek_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "  secret-key  ")
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    assert get_deepseek_key() == "secret-key"


def test_get_deepseek_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    key_file = tmp_path / "key.txt"
    key_file.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY_FILE", str(key_file))
    assert get_deepseek_key() == "file-key"


def test_get_deepseek_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    assert get_deepseek_key() is None


# ---------------------------------------------------------------------------
# 变量展开
# ---------------------------------------------------------------------------


def test_expand_file_reference(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-file-key\n", encoding="utf-8")
    assert expand_value(f"{{file:{key_file}}}") == "sk-file-key"


def test_expand_env_reference(monkeypatch):
    monkeypatch.setenv("LGSKILLS_TEST_KEY", "sk-env-key")
    assert expand_value("{env:LGSKILLS_TEST_KEY}") == "sk-env-key"


def test_expand_plain_value_unchanged():
    assert expand_value("plain text") == "plain text"
    assert expand_value(42) == 42


def test_expand_config_recursive(tmp_path):
    key_file = tmp_path / "k.txt"
    key_file.write_text("sk-k", encoding="utf-8")
    data = {"provider": {"deepseek": {"options": {"apiKey": f"{{file:{key_file}}}", "baseURL": "https://x"}}}}
    expanded = expand_config(data)
    assert expanded["provider"]["deepseek"]["options"]["apiKey"] == "sk-k"
    assert expanded["provider"]["deepseek"]["options"]["baseURL"] == "https://x"


# ---------------------------------------------------------------------------
# 三层配置合并
# ---------------------------------------------------------------------------


def test_load_config_merge_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    global_cfg = tmp_path / "global.json"
    project_cfg = tmp_path / "lgskills.json"
    global_cfg.write_text(json.dumps({"model": "deepseek/deepseek-chat", "temperature": 0.2}), encoding="utf-8")
    project_cfg.write_text(json.dumps({"model": "openai/gpt-4o"}), encoding="utf-8")

    merged = load_config(global_path=global_cfg, project_path=project_cfg)
    # 项目覆盖全局
    assert merged["model"] == "openai/gpt-4o"
    # 全局的非冲突键保留
    assert merged["temperature"] == 0.2


def test_load_config_no_files(tmp_path):
    merged = load_config(global_path=tmp_path / "nope.json", project_path=tmp_path / "nope2.json")
    assert merged == {}


# ---------------------------------------------------------------------------
# Settings.load 路径注入
# ---------------------------------------------------------------------------


def test_settings_load_from_config(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-cfg-key", encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "model": "openai/gpt-4o",
                "provider": {
                    "openai": {
                        "models": {"gpt-4o": {}},
                        "options": {"apiKey": f"{{file:{key_file}}}", "baseURL": "https://api.openai.com/v1"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("LGSKILLS_MODEL", raising=False)
    s = Settings.load(global_path=cfg, project_path=None)
    assert s.provider == "openai"
    assert s.model == "gpt-4o"
    assert s.api_key == "sk-cfg-key"
    assert s.base_url == "https://api.openai.com/v1"


def test_parse_model_ref():
    assert parse_model_ref("deepseek/deepseek-chat") == ("deepseek", "deepseek-chat")
    assert parse_model_ref("gpt-4o") == ("deepseek", "gpt-4o")  # 无 provider 时默认 deepseek
