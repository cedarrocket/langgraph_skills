from langchain_core.messages import AIMessage, HumanMessage

from langgraph_skills.graph import print_help
from langgraph_skills.models import AgentState, InputOption
from langgraph_skills.nodes import generic_router, tool_router


def _state(**overrides) -> AgentState:
    base = AgentState(
        messages=[HumanMessage(content="hi")],
        global_instructions="",
        state_instructions="",
        deliverables={},
        current_node="A",
        next_state="B",
        loop_count=0,
        max_loops=10,
    )
    base.update(overrides)
    return base


def test_router_next_state():
    assert generic_router(_state()) == "B"


def test_router_tool_call_routes_to_tools():
    s = _state(messages=[AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "1"}])])
    assert generic_router(s) == "tools"


def test_router_submit_result_ignored():
    # SubmitResult 是跳转工具，不应路由到 tools
    s = _state(messages=[AIMessage(content="", tool_calls=[{"name": "SubmitResult", "args": {}, "id": "1"}])])
    assert generic_router(s) == "B"


def test_router_loop_limit_forces_end():
    s = _state(loop_count=10, max_loops=10)
    assert generic_router(s) == "__end__"


def test_router_no_next_state_ends():
    s = _state(next_state="")
    assert generic_router(s) == "__end__"


def test_tool_router_returns_current_node():
    assert tool_router(_state()) == "A"


def test_tool_router_empty_ends():
    s = _state(current_node="")
    assert tool_router(s) == "__end__"


def test_print_help_lists_options(capsys):
    opts = [InputOption(name="input_path", help="Input file.", reader="txt_reader")]
    print_help("myskill.md", opts)
    err = capsys.readouterr().err
    assert "lgskills run myskill.md" in err
    assert "--input_path" in err
