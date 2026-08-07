"""Trigger（触发器）机制测试。

覆盖：表达式求值、pyfunction 条件、静态检查（语法/未定义变量）、
triggers.json 加载、post_node/pre_llm 检查点、handler 执行。
"""

import json

import pytest
from langchain_core.messages import HumanMessage

from langgraph_skills.config import load_triggers
from langgraph_skills.triggers import (
    CHECKPOINT_POST_NODE,
    CHECKPOINT_PRE_LLM,
    Trigger,
    TriggerError,
    check_condition_expr,
    evaluate_condition,
    load_triggers_from_config,
    run_handler,
    triggers_for_checkpoint,
)

# ---------------------------------------------------------------------------
# 表达式静态检查
# ---------------------------------------------------------------------------


def test_check_expr_valid():
    check_condition_expr("context_length > 100 and loop_count < 5")


def test_check_expr_undefined_variable():
    with pytest.raises(TriggerError, match="undefined variable"):
        check_condition_expr("undefined_var > 5")


def test_check_expr_syntax_error():
    with pytest.raises(TriggerError, match="syntax error"):
        check_condition_expr("loop_count >>")


def test_check_expr_pyfunction_skipped():
    check_condition_expr("pyfunction:any.py")  # 不检查


# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------


def test_evaluate_expression_true():
    t = Trigger(condition="loop_count > 1")
    assert evaluate_condition(t, {"loop_count": 2, "current_node": "A"}) is True
    assert evaluate_condition(t, {"loop_count": 1, "current_node": "A"}) is False


def test_evaluate_disabled_trigger():
    t = Trigger(condition="loop_count > 1", enabled=False)
    assert evaluate_condition(t, {"loop_count": 5}) is False


def test_evaluate_pyfunction(tmp_path):
    script = tmp_path / "check.py"
    script.write_text("if loop_count >= 2:\n    trigger_result(True)\n", encoding="utf-8")
    t = Trigger(condition=f"pyfunction:{script}")
    assert evaluate_condition(t, {"loop_count": 2, "deliverables": {}}) is True
    assert evaluate_condition(t, {"loop_count": 1, "deliverables": {}}) is False


def test_evaluate_pyfunction_missing_file():
    t = Trigger(condition="pyfunction:no_such_file.py")
    assert evaluate_condition(t, {}) is False


# ---------------------------------------------------------------------------
# 触发器列表加载
# ---------------------------------------------------------------------------


def test_load_triggers_from_config():
    cfg = {
        "triggers": [
            {"condition": "loop_count > 5", "on_trigger": "h.py", "checkpoint": "post_node"},
            {"condition": "context_length > 1000", "on_trigger": "h2.py"},
        ]
    }
    triggers = load_triggers_from_config(cfg)
    assert len(triggers) == 2
    assert triggers[0].checkpoint == CHECKPOINT_POST_NODE
    assert triggers[1].checkpoint == CHECKPOINT_PRE_LLM  # 默认


def test_load_triggers_from_config_static_check():
    with pytest.raises(TriggerError, match="undefined variable"):
        load_triggers_from_config({"triggers": [{"condition": "nope > 1", "on_trigger": "h.py"}]})


def test_load_triggers_merges_global_and_project(tmp_path):
    global_file = tmp_path / "global_triggers.json"
    project_file = tmp_path / "project_triggers.json"
    global_file.write_text(
        json.dumps({"triggers": [{"condition": "loop_count > 3", "on_trigger": "g.py"}]}),
        encoding="utf-8",
    )
    project_file.write_text(
        json.dumps({"triggers": [{"condition": "error_flag", "on_trigger": "p.py"}]}),
        encoding="utf-8",
    )
    merged = load_triggers(global_path=global_file, project_path=project_file)
    assert len(merged) == 2


def test_triggers_for_checkpoint():
    triggers = [
        Trigger(condition="a", checkpoint=CHECKPOINT_PRE_LLM),
        Trigger(condition="b", checkpoint=CHECKPOINT_POST_NODE),
    ]
    pre = triggers_for_checkpoint(triggers, CHECKPOINT_PRE_LLM)
    post = triggers_for_checkpoint(triggers, CHECKPOINT_POST_NODE)
    assert len(pre) == 1 and pre[0].condition == "a"
    assert len(post) == 1 and post[0].condition == "b"


# ---------------------------------------------------------------------------
# handler 执行
# ---------------------------------------------------------------------------


def test_run_handler_modifies_deliverables(tmp_path):
    handler = tmp_path / "h.py"
    handler.write_text(
        'print("  [HANDLER] fired")\ndeliverables["triggered"] = True\n',
        encoding="utf-8",
    )
    scope = {"deliverables": {}, "messages": [], "loop_count": 1, "current_node": "A"}
    run_handler(str(handler), scope)
    assert scope["deliverables"]["triggered"] is True


def test_run_handler_missing_file():
    run_handler("no_such.py", {})  # 不抛异常，打印警告


def test_run_handler_error_tolerated(tmp_path):
    handler = tmp_path / "bad.py"
    handler.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    run_handler(str(handler), {"deliverables": {}})  # 不抛异常


# ---------------------------------------------------------------------------
# 文本压缩（handler 裁剪 messages）
# ---------------------------------------------------------------------------


def test_run_handler_compacts_messages_by_reference(tmp_path):
    """handler 通过引用修改 messages 应真实作用于图状态（文本压缩）。"""
    handler = tmp_path / "compact.py"
    handler.write_text("del messages[:-2]\n", encoding="utf-8")
    state_messages = [HumanMessage(content=f"msg{i}") for i in range(5)]
    scope = {
        "deliverables": {},
        "messages": state_messages,  # 同一引用（如 state["messages"]）
        "loop_count": 2,
        "current_node": "A",
    }
    run_handler(str(handler), scope)
    assert len(state_messages) == 2  # 真实状态被裁剪


def test_run_handler_reassign_messages_does_not_affect_state(tmp_path):
    """handler 用 `messages = [...]` 重新赋值是局部变量，不影响图状态（易踩的坑）。"""
    handler = tmp_path / "reassign.py"
    handler.write_text("messages = ['only one']\n", encoding="utf-8")
    state_messages = [HumanMessage(content="m1"), HumanMessage(content="m2")]
    scope = {"deliverables": {}, "messages": state_messages, "loop_count": 1, "current_node": "A"}
    run_handler(str(handler), scope)
    assert len(state_messages) == 2  # 重新赋值不生效


# ---------------------------------------------------------------------------
# compact() 注入（安全的上下文压缩接口）
# ---------------------------------------------------------------------------


def test_run_handler_compact_api(tmp_path):
    """handler 用 compact(keep_last) 应安全裁剪真实图状态。"""
    handler = tmp_path / "compact_api.py"
    handler.write_text("n = compact(2)\n", encoding="utf-8")
    state_messages = [HumanMessage(content=f"m{i}") for i in range(8)]
    scope = {"deliverables": {}, "messages": state_messages, "loop_count": 1, "current_node": "A"}
    run_handler(str(handler), scope)
    assert len(state_messages) == 2  # compact 用切片赋值，真实生效


def test_run_handler_compact_negative_rejected(tmp_path):
    """compact 负数应报错（不静默）。"""
    handler = tmp_path / "bad_compact.py"
    handler.write_text("compact(-1)\n", encoding="utf-8")
    state_messages = [HumanMessage(content="m0")]
    scope = {"deliverables": {}, "messages": state_messages, "loop_count": 1, "current_node": "A"}
    run_handler(str(handler), scope)  # 异常被捕获，不抛出
    assert len(state_messages) == 1  # 未裁剪


def test_evaluate_pyfunction_has_compact(tmp_path):
    """pyfunction 条件环境也应注入 compact()。"""
    script = tmp_path / "check_compact.py"
    script.write_text("compact(1)\nif len(messages) == 1:\n    trigger_result(True)\n", encoding="utf-8")
    t = Trigger(condition=f"pyfunction:{script}")
    state_messages = [HumanMessage(content=f"m{i}") for i in range(5)]
    scope = {"deliverables": {}, "messages": state_messages, "loop_count": 2, "current_node": "A"}
    assert evaluate_condition(t, scope) is True
    assert len(state_messages) == 1


# ---------------------------------------------------------------------------
# 集成：create_node 的 post_node 检查点
# ---------------------------------------------------------------------------


def test_post_node_checkpoint_fires_in_node(tmp_path):
    from langgraph_skills import executors as ex_mod
    from langgraph_skills.executors import ExecutorResult
    from langgraph_skills.models import AgentState, NodeInfo
    from langgraph_skills.nodes import create_node
    from langgraph_skills.tools import ToolRegistry

    handler = tmp_path / "handler.py"
    handler.write_text('deliverables["fired"] = True\n', encoding="utf-8")
    trigger = Trigger(
        condition="loop_count > 1",
        on_trigger=str(handler),
        checkpoint=CHECKPOINT_POST_NODE,
    )

    # 注册一个假的 code 执行器（避免真实 LLM）
    def fake_code(ctx):
        return ExecutorResult(next_state=None, payload=None)

    old = ex_mod.EXECUTOR_REGISTRY.get("code")
    ex_mod.EXECUTOR_REGISTRY["code"] = fake_code
    try:
        fn = create_node(
            NodeInfo(name="A", node_type="code"),
            ToolRegistry(),
            safe_input=lambda p: "",
            run_skill=lambda *a, **k: {},
            triggers=[trigger],
        )
        state = AgentState(
            messages=[],
            global_instructions="",
            state_instructions="",
            deliverables={},
            current_node="A",
            next_state="",
            loop_count=1,  # 本轮 +1 → 2 > 1 触发
            max_loops=10,
        )
        ret = fn(state)
        assert ret["deliverables"].get("fired") is True
    finally:
        if old is None:
            ex_mod.EXECUTOR_REGISTRY.pop("code", None)
        else:
            ex_mod.EXECUTOR_REGISTRY["code"] = old


def test_post_node_checkpoint_not_fired_below_threshold(tmp_path):
    from langgraph_skills import executors as ex_mod
    from langgraph_skills.executors import ExecutorResult
    from langgraph_skills.models import AgentState, NodeInfo
    from langgraph_skills.nodes import create_node
    from langgraph_skills.tools import ToolRegistry

    handler = tmp_path / "handler.py"
    handler.write_text('deliverables["fired"] = True\n', encoding="utf-8")
    trigger = Trigger(
        condition="loop_count > 10",
        on_trigger=str(handler),
        checkpoint=CHECKPOINT_POST_NODE,
    )

    def fake_code(ctx):
        return ExecutorResult(next_state=None, payload=None)

    old = ex_mod.EXECUTOR_REGISTRY.get("code")
    ex_mod.EXECUTOR_REGISTRY["code"] = fake_code
    try:
        fn = create_node(
            NodeInfo(name="A", node_type="code"),
            ToolRegistry(),
            safe_input=lambda p: "",
            run_skill=lambda *a, **k: {},
            triggers=[trigger],
        )
        state = AgentState(
            messages=[],
            global_instructions="",
            state_instructions="",
            deliverables={},
            current_node="A",
            next_state="",
            loop_count=1,
            max_loops=10,
        )
        ret = fn(state)
        assert ret["deliverables"].get("fired") is None  # 未触发
    finally:
        if old is None:
            ex_mod.EXECUTOR_REGISTRY.pop("code", None)
        else:
            ex_mod.EXECUTOR_REGISTRY["code"] = old
