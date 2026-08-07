"""CLI 端到端测试（mock sys.argv，捕获 SystemExit 断言退出码）。"""

import sys

from langgraph_skills.cli import main


def _run_cli(monkeypatch, args, capsys):
    """执行 cli.main，返回 (exit_code, stderr, stdout)。未退出时为 None。"""
    monkeypatch.setattr(sys, "argv", ["lgskills"] + args)
    try:
        main()
        code = None  # 正常返回（如 compile 成功）
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.err, out.out


def _isolate_config(monkeypatch, tmp_path):
    """隔离配置读取：让全局/项目配置路径指向不存在的位置，避免受机器真实配置影响。"""
    none_path = tmp_path / "no-config.json"
    monkeypatch.setattr("langgraph_skills.config._global_config_path", lambda: none_path)
    monkeypatch.setattr("langgraph_skills.config._project_config_path", lambda: None)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_ok(monkeypatch, capsys, tmp_path):
    skill = tmp_path / "ok.md"
    skill.write_text("# [Node] A\n- **is_final**: true\n", encoding="utf-8")
    code, err, out = _run_cli(monkeypatch, ["validate", str(skill)], capsys)
    assert code == 0
    assert "OK" in err


def test_validate_error_exit_2(monkeypatch, capsys, tmp_path):
    # 悬空跳转 → 校验失败，exit 2
    skill = tmp_path / "bad.md"
    skill.write_text(
        "# [Node] A\n## [Transitions]\n- Default -> NonExistent\n", encoding="utf-8"
    )
    code, err, out = _run_cli(monkeypatch, ["validate", str(skill)], capsys)
    assert code == 2
    assert "non-existent" in err


def test_validate_missing_file_exit_2(monkeypatch, capsys, tmp_path):
    code, err, out = _run_cli(monkeypatch, ["validate", str(tmp_path / "nope.md")], capsys)
    assert code == 2
    assert "Parsing error" in err


# ---------------------------------------------------------------------------
# run（无 API key 降级路径）
# ---------------------------------------------------------------------------


def test_run_without_key_skips_execution(monkeypatch, capsys, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    skill = tmp_path / "ok.md"
    skill.write_text("# [Node] A\n- **is_final**: true\n", encoding="utf-8")
    code, err, out = _run_cli(monkeypatch, ["run", str(skill), "hi"], capsys)
    assert code == 0
    assert "skipping execution" in err


# ---------------------------------------------------------------------------
# --help / shebang fallback
# ---------------------------------------------------------------------------


def test_help_exit_0(monkeypatch, capsys):
    code, err, out = _run_cli(monkeypatch, ["--help"], capsys)
    assert code == 0


def test_shebang_fallback_routes_to_run(monkeypatch, capsys, tmp_path):
    """无子命令但传 .md 文件 → 自动路由到 run。"""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    skill = tmp_path / "ok.md"
    skill.write_text("# [Node] A\n- **is_final**: true\n", encoding="utf-8")
    code, err, out = _run_cli(monkeypatch, [str(skill)], capsys)
    assert code == 0
    assert "skipping execution" in err


# ---------------------------------------------------------------------------
# compile（无 key 时降级为复制草稿）
# ---------------------------------------------------------------------------


def test_compile_without_key_copies_draft(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    draft = tmp_path / "draft.md"
    draft.write_text("# [Node] A\n- **is_final**: true\n", encoding="utf-8")
    out_path = tmp_path / "compiled.md"
    code, err, out = _run_cli(monkeypatch, ["compile", str(draft), str(out_path)], capsys)
    assert code is None  # compile 成功不 sys.exit
    assert out_path.read_text(encoding="utf-8") == draft.read_text(encoding="utf-8")
    assert "Compiled skill saved" in out  # compiler 用 print 输出到 stdout


# ---------------------------------------------------------------------------
# model 子命令
# ---------------------------------------------------------------------------


def test_model_list_shows_default_provider(monkeypatch, capsys, tmp_path):
    # 隔离全局配置路径（避免读真实 ~/.config）
    monkeypatch.setattr("langgraph_skills.model_cmd._global_config_path", lambda: tmp_path / "cfg.json")
    monkeypatch.setattr("langgraph_skills.model_cmd._find_project_config", lambda: None)
    code, err, out = _run_cli(monkeypatch, ["model", "list"], capsys)
    assert code is None
    assert "deepseek" in out
    assert "deepseek-chat" in out


def test_model_set_writes_config(monkeypatch, capsys, tmp_path):
    cfg_path = tmp_path / "cfg.json"
    monkeypatch.setattr("langgraph_skills.model_cmd._global_config_path", lambda: cfg_path)
    code, err, out = _run_cli(monkeypatch, ["model", "set", "openai/gpt-4o"], capsys)
    assert code is None
    assert cfg_path.exists()
    import json

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["model"] == "openai/gpt-4o"


def test_model_config_shows_settings(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("langgraph_skills.model_cmd._global_config_path", lambda: tmp_path / "cfg.json")
    monkeypatch.setattr("langgraph_skills.model_cmd._find_project_config", lambda: None)
    monkeypatch.setattr("langgraph_skills.config._global_config_path", lambda: tmp_path / "cfg.json")
    monkeypatch.setattr("langgraph_skills.config._project_config_path", lambda: None)
    code, err, out = _run_cli(monkeypatch, ["model", "config"], capsys)
    assert code is None
    assert "deepseek" in out


def test_model_import_opencode_missing_config(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("langgraph_skills.model_cmd.OPENCODE_GLOBAL", tmp_path / "none.json")
    monkeypatch.setattr("langgraph_skills.model_cmd._global_config_path", lambda: tmp_path / "cfg.json")
    code, err, out = _run_cli(monkeypatch, ["model", "import-opencode"], capsys)
    assert code is None
    assert "not found" in err


def test_model_import_opencode_success(monkeypatch, capsys, tmp_path):
    import json as json_mod

    opencode_cfg = tmp_path / "opencode.json"
    opencode_cfg.write_text(
        json_mod.dumps(
            {
                "provider": {
                    "anthropic": {
                        "models": {"claude-sonnet-4-5": {}},
                        "options": {"apiKey": "{env:ANTHROPIC_API_KEY}", "baseURL": "https://api.anthropic.com/v1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg_path = tmp_path / "cfg.json"
    monkeypatch.setattr("langgraph_skills.model_cmd.OPENCODE_GLOBAL", opencode_cfg)
    monkeypatch.setattr("langgraph_skills.model_cmd._global_config_path", lambda: cfg_path)
    code, err, out = _run_cli(monkeypatch, ["model", "import-opencode"], capsys)
    assert code is None
    assert "anthropic" in out
    assert cfg_path.exists()
    data = json_mod.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["provider"]["anthropic"]["models"] == {"claude-sonnet-4-5": {}}
