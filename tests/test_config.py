import pytest

from langgraph_skills.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    Settings,
    get_deepseek_key,
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
