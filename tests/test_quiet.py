"""quiet 静默模式测试：调试日志开关控制。"""


import langgraph_skills.debug as debug_mod


def test_debug_print_default_enabled():
    assert debug_mod.DEBUG_PRINT_ENABLED is True


def test_set_debug_print_toggle():
    debug_mod.set_debug_print(False)
    assert debug_mod.DEBUG_PRINT_ENABLED is False
    debug_mod.set_debug_print(True)
    assert debug_mod.DEBUG_PRINT_ENABLED is True


def test_run_skill_quiet_toggles_global(tmp_path):
    """run_skill(quiet=True) 设置全局调试开关为关闭。"""
    p = tmp_path / "skill.md"
    p.write_text("# [Node] A\n- **is_final**: true\n", encoding="utf-8")
    from langgraph_skills.runner import run_skill

    try:
        # quiet 模式：开关应被关闭
        run_skill(str(p), quiet=True)
        assert debug_mod.DEBUG_PRINT_ENABLED is False
    finally:
        debug_mod.set_debug_print(True)
