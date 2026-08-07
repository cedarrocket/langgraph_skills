"""create_node 节点工厂的单元测试（mock 执行器，测通用管道）。

被测对象是节点级通用逻辑：loop 计数、payload 传递、JSON Schema 自愈、
人工审批门、未知类型报错、注入缺失报错。执行器用假执行器精确控制。
"""

import pytest

from langgraph_skills import executors as ex_mod
from langgraph_skills.executors import ExecutorContext, ExecutorResult
from langgraph_skills.models import AgentState, NodeInfo, Transition
from langgraph_skills.nodes import create_node
from langgraph_skills.tools import ToolRegistry

FAKE_TYPE = "fake_node"


class FakeExecutor:
    """可编程假执行器：每次执行返回预设结果。"""

    def __init__(self, result=None):
        self.calls = 0
        self.last_ctx = None
        self.result = result or ExecutorResult(next_state="Finish", payload="out")

    def __call__(self, ctx: ExecutorContext) -> ExecutorResult:
        self.calls += 1
        self.last_ctx = ctx
        return self.result


@pytest.fixture(autouse=True)
def _clean_registry():
    """注册/清理假执行器，避免污染全局注册表。"""
    old = ex_mod.EXECUTOR_REGISTRY.get(FAKE_TYPE)
    ex_mod.register_executor(FAKE_TYPE, lambda ctx: ExecutorResult(next_state="Finish", payload="out"))
    yield
    if old is None:
        ex_mod.EXECUTOR_REGISTRY.pop(FAKE_TYPE, None)
    else:
        ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = old


def _state(node_name="A", loop_count=0, max_loops=10, deliverables=None):
    return AgentState(
        messages=[],
        global_instructions="",
        state_instructions="",
        deliverables=deliverables or {},
        current_node=node_name,
        next_state="",
        loop_count=loop_count,
        max_loops=max_loops,
    )


def _node(name="A", transitions=None, is_final=False, interactive=False, output_schema=None):
    return NodeInfo(
        name=name,
        instructions="",
        transitions=transitions or [],
        is_final=is_final,
        interactive=interactive,
        output_schema=output_schema,
        node_type=FAKE_TYPE,  # 用假执行器类型，避免走真实 LLM
    )


# ---------------------------------------------------------------------------
# loop 计数
# ---------------------------------------------------------------------------


def test_loop_count_increments():
    fake = FakeExecutor()
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(_node(), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state(loop_count=3))
    assert ret["loop_count"] == 4
    assert ret["current_node"] == "A"


def test_interactive_raises_max_loops():
    fake = FakeExecutor()
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(_node(interactive=True), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state(max_loops=5))
    # interactive 节点把上限抬到至少 20
    assert ret["max_loops"] == 20


def test_payload_passed_to_deliverables():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload="data"))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(_node(transitions=[Transition(next="B")]), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state())
    assert ret["deliverables"]["payload"] == "data"
    assert ret["next_state"] == "B"


# ---------------------------------------------------------------------------
# JSON Schema 自愈
# ---------------------------------------------------------------------------


def test_json_schema_valid_payload_keeps_transition():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload='{"x": "ok"}'))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    fn = create_node(_node(transitions=[Transition(next="B")], output_schema=schema), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state())
    assert ret["next_state"] == "B"


def test_json_schema_invalid_payload_routes_back():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload="not json"))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    fn = create_node(_node(transitions=[Transition(next="B")], output_schema=schema), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state())
    # 非法 JSON → 回退本节点 + 注入错误提示
    assert ret["next_state"] == "A"
    assert "JSON validation failed" in ret["deliverables"]["payload"]


def test_json_schema_mismatch_routes_back():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload='{"y": 1}'))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    fn = create_node(_node(transitions=[Transition(next="B")], output_schema=schema), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    ret = fn(_state())
    assert ret["next_state"] == "A"
    assert "JSON validation failed against schema" in ret["deliverables"]["payload"]


# ---------------------------------------------------------------------------
# 人工审批门
# ---------------------------------------------------------------------------


def test_approval_approved_proceeds():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload="data"))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(
        _node(transitions=[Transition(next="B", require_approval=True)]),
        ToolRegistry(),
        safe_input=lambda p: "y",  # 批准
        run_skill=lambda *a, **k: {},
    )
    ret = fn(_state())
    assert ret["next_state"] == "B"


def test_approval_rejected_routes_back():
    fake = FakeExecutor(result=ExecutorResult(next_state="B", payload="data"))
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(
        _node(transitions=[Transition(next="B", require_approval=True)]),
        ToolRegistry(),
        safe_input=lambda p: "n",  # 拒绝
        run_skill=lambda *a, **k: {},
    )
    ret = fn(_state())
    assert ret["next_state"] == "A"  # 回退本节点
    assert "rejected by the user" in ret["deliverables"]["payload"]


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_unknown_node_type_raises():
    info = NodeInfo(name="X", node_type="nonexistent")
    fn = create_node(info, ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    with pytest.raises(ValueError, match="Unknown state type"):
        fn(_state())


def test_missing_injection_raises():
    fake = FakeExecutor()
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(_node(), ToolRegistry())  # 不注入 safe_input/run_skill
    with pytest.raises(RuntimeError, match="must be injected"):
        fn(_state())


# ---------------------------------------------------------------------------
# 执行器调用契约
# ---------------------------------------------------------------------------


def test_executor_receives_context():
    fake = FakeExecutor()
    ex_mod.EXECUTOR_REGISTRY[FAKE_TYPE] = fake
    fn = create_node(_node(), ToolRegistry(), safe_input=lambda p: "y", run_skill=lambda *a, **k: {})
    fn(_state())
    assert fake.last_ctx is not None
    assert fake.last_ctx.node_info.name == "A"
