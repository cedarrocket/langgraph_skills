from langgraph_skills.executors import (
    ExecutorContext,
    ExecutorResult,
    execute_code,
    execute_script,
    execute_skill,
    get_executor,
    register_executor,
)
from langgraph_skills.models import AgentState, NodeInfo
from langgraph_skills.tools import ToolRegistry


def _ctx(node_info: NodeInfo, state: AgentState | None = None, run_skill=None):
    return ExecutorContext(
        node_info=node_info,
        state=state
        or AgentState(
            messages=[],
            global_instructions="",
            state_instructions="",
            deliverables={},
            current_node=node_info.name,
            next_state="",
            loop_count=0,
            max_loops=10,
        ),
        tools=ToolRegistry(),
        safe_input=lambda p: "",
        run_skill=run_skill or (lambda *a, **k: {}),
    )


def test_execute_code_transition():
    info = NodeInfo(
        name="Check",
        node_type="code",
        instructions="```python\ntransition_to('Win', '42')\n```",
    )
    result = execute_code(_ctx(info))
    assert result.next_state == "Win"
    assert result.payload == "42"


def test_execute_code_uses_deliverables():
    info = NodeInfo(
        name="Read",
        node_type="code",
        instructions="```python\np = get_payload()\ntransition_to('Out', p.upper())\n```",
    )
    state = AgentState(
        messages=[],
        global_instructions="",
        state_instructions="",
        deliverables={"payload": "abc"},
        current_node="Read",
        next_state="",
        loop_count=0,
        max_loops=10,
    )
    result = execute_code(_ctx(info, state=state))
    assert result.next_state == "Out"
    assert result.payload == "ABC"


def test_execute_script(tmp_path):
    script = tmp_path / "step.py"
    script.write_text("import sys\ntransition_to('Done', 'from-script')\n", encoding="utf-8")
    info = NodeInfo(name="Run", node_type="script", src=str(script))
    result = execute_script(_ctx(info))
    assert result.next_state == "Done"
    assert result.payload == "from-script"


def test_execute_script_missing_src_raises():
    info = NodeInfo(name="Bad", node_type="script")
    try:
        execute_script(_ctx(info))
        assert False, "should raise"
    except ValueError as e:
        assert "missing the 'src'" in str(e)


def test_execute_skill_uses_run_skill_and_payload(tmp_path):
    captured = {}

    child = tmp_path / "child_skill.md"
    child.write_text("# [Node] S\n- **is_final**: true\n", encoding="utf-8")

    def fake_run_skill(skill_path, user_input="", initial_deliverables=None, initial_messages=None):
        captured["path"] = skill_path
        captured["payload"] = initial_deliverables.get("payload")
        return {"payload": "child-result"}

    info = NodeInfo(
        name="Child",
        node_type="skill",
        src=str(child),
        transitions=[],
    )
    # skill 执行器需要至少一个 transition 来定 next_state
    info.transitions = [type("T", (), {"next": "Report"})()]
    state = AgentState(
        messages=[],
        global_instructions="",
        state_instructions="",
        deliverables={"payload": "parent-input"},
        current_node="Child",
        next_state="",
        loop_count=0,
        max_loops=10,
    )
    result = execute_skill(_ctx(info, state=state, run_skill=fake_run_skill))
    assert captured["path"] == str(child)
    assert captured["payload"] == "parent-input"
    assert result.next_state == "Report"
    assert result.payload == "child-result"


def test_default_executors_registered():
    for t in ("llm", "code", "script", "skill"):
        assert get_executor(t) is not None


def test_register_executor_extension():
    def wait_executor(ctx: ExecutorContext) -> ExecutorResult:
        return ExecutorResult(next_state=None, payload="waited")

    register_executor("wait", wait_executor)
    assert get_executor("wait") is wait_executor
    result = wait_executor(_ctx(NodeInfo(name="W", node_type="wait")))
    assert result.payload == "waited"
