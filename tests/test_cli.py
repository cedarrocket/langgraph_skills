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
