"""execute_llm 的单元测试（mock ChatOpenAI，无真实 API 调用）。

被测对象是 execute_llm 的**自有逻辑**：prompt 构造、工具绑定、SubmitResult 解析、
无条件跳转、交互分支、history_window 切片。LLM 响应用 FakeLLM 精确控制。
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from langgraph_skills import executors as ex
from langgraph_skills.executors import ExecutorContext, execute_llm
from langgraph_skills.models import AgentState, NodeInfo, Transition
from langgraph_skills.tools import ToolRegistry


class FakeLLM:
    """模拟 ChatOpenAI：bind_tools 返回自身，invoke 依次弹出预设响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.invoke_calls = []          # 每次 invoke 收到的 messages
        self.bind_tools_calls = []      # 每次 bind_tools 收到的 (tools, tool_choice)

    def bind_tools(self, tools, tool_choice=None):
        self.bind_tools_calls.append((tools, tool_choice))
        return self

    def invoke(self, messages):
        self.invoke_calls.append(messages)
        resp = self.responses.pop(0)
        return AIMessage(content=resp.get("content", ""), tool_calls=resp.get("tool_calls") or [])


def _mk_llm_response(content="answer", tool_calls=None):
    return {"content": content, "tool_calls": tool_calls}


def _ctx(node_info, fake_llm, state=None):
    ctx = ExecutorContext(
        node_info=node_info,
        state=state
        or AgentState(
            messages=[HumanMessage(content="user")],
            global_instructions="GLOBAL",
            state_instructions="",
            deliverables={},
            current_node=node_info.name,
            next_state="",
            loop_count=0,
            max_loops=10,
        ),
        tools=ToolRegistry(),
        safe_input=lambda p: "user reply",
        run_skill=lambda *a, **k: {},
    )
    # 用 monkeypatch 思路：直接替换模块内的 ChatOpenAI
    ex.ChatOpenAI = lambda *a, **k: fake_llm
    return ctx


@pytest.fixture(autouse=True)
def _restore_chatopenai():
    original = ex.ChatOpenAI
    yield
    ex.ChatOpenAI = original


def _node(name="N", *, is_final=False, interactive=False, transitions=None, tools=None, output_schema=None, history_window=None):
    return NodeInfo(
        name=name,
        instructions="do the task",
        transitions=transitions or [],
        is_final=is_final,
        interactive=interactive,
        tools=tools or [],
        output_schema=output_schema,
        history_window=history_window,
    )


# ---------------------------------------------------------------------------
# prompt 构造
# ---------------------------------------------------------------------------


def test_prompt_contains_global_and_node_instructions():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(transitions=[Transition(next="Finish")])
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    sys_prompt = llm.invoke_calls[0][0].content
    assert "GLOBAL" in sys_prompt
    assert "[Current Task: N]" in sys_prompt
    assert "do the task" in sys_prompt


def test_prompt_contains_output_schema():
    llm = FakeLLM([_mk_llm_response()])
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    info = _node(transitions=[Transition(next="Finish")], output_schema=schema)
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    sys_prompt = llm.invoke_calls[0][0].content
    assert "[Output JSON Schema Constraint]" in sys_prompt
    assert '"x"' in sys_prompt


def test_prompt_contains_deliverables_and_payload():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(transitions=[Transition(next="Finish")])
    state = AgentState(
        messages=[],
        global_instructions="",
        state_instructions="",
        deliverables={"topic": "ai", "payload": "prev output", "feedback": "fix it"},
        current_node="N",
        next_state="",
        loop_count=0,
        max_loops=10,
    )
    ctx = _ctx(info, llm, state=state)
    execute_llm(ctx)

    sys_prompt = llm.invoke_calls[0][0].content
    assert "topic: ai" in sys_prompt
    assert "[Context from previous stage]" in sys_prompt
    assert "prev output" in sys_prompt
    assert "[Feedback from transition]" in sys_prompt


def test_prompt_directive_when_conditional():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(transitions=[Transition(condition="c1", next="A"), Transition(condition="c2", next="B")])
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    sys_prompt = llm.invoke_calls[0][0].content
    assert "SubmitResult" in sys_prompt
    assert "Valid options" in sys_prompt
    # 有条件跳转必须强制工具调用
    assert llm.bind_tools_calls[0][1] == "required"


# ---------------------------------------------------------------------------
# 工具绑定
# ---------------------------------------------------------------------------


def test_bind_tools_when_conditional():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(transitions=[Transition(condition="c", next="A")])
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    assert len(llm.bind_tools_calls) == 1
    tools, tool_choice = llm.bind_tools_calls[0]
    assert tool_choice == "required"


def test_no_bind_tools_for_final_unconditional():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(is_final=True)
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    # final + 无工具 → 不 bind，直接 invoke
    assert llm.bind_tools_calls == []
    assert len(llm.invoke_calls) == 1


# ---------------------------------------------------------------------------
# SubmitResult 解析
# ---------------------------------------------------------------------------


def test_submit_result_transitions():
    llm = FakeLLM(
        [
            _mk_llm_response(
                tool_calls=[
                    {"name": "SubmitResult", "args": {"next_state": "B", "payload": "data"}, "id": "t1"}
                ]
            )
        ]
    )
    info = _node(transitions=[Transition(condition="c", next="A"), Transition(condition="d", next="B")])
    ctx = _ctx(info, llm)
    result = execute_llm(ctx)

    assert result.next_state == "B"
    assert result.payload == "data"
    # 输出消息包含 LLM 响应 + 一个 ToolMessage 确认
    assert len(result.output_messages) == 2


def test_submit_result_sets_feedback():
    llm = FakeLLM(
        [
            _mk_llm_response(
                tool_calls=[
                    {"name": "SubmitResult", "args": {"next_state": "B", "payload": ""}, "id": "t1"}
                ]
            )
        ]
    )
    info = _node(transitions=[Transition(condition="c", next="B", feedback="go to B")])
    ctx = _ctx(info, llm)
    execute_llm(ctx)

    assert ctx.state["deliverables"].get("feedback") == "go to B"


# ---------------------------------------------------------------------------
# 无条件跳转 / 自动流转
# ---------------------------------------------------------------------------


def test_unconditional_auto_transition():
    llm = FakeLLM([_mk_llm_response(content="the answer")])
    info = _node(transitions=[Transition(next="Finish")])
    ctx = _ctx(info, llm)
    result = execute_llm(ctx)

    assert result.next_state == "Finish"
    assert result.payload == "the answer"


# ---------------------------------------------------------------------------
# 交互分支
# ---------------------------------------------------------------------------


def test_interactive_loops_back_when_no_transition():
    llm = FakeLLM([_mk_llm_response(content="AI text")])
    info = _node(interactive=True)
    ctx = _ctx(info, llm)
    result = execute_llm(ctx)

    # 无跳转触发 → 回本节点，追加用户输入
    assert result.next_state == "N"
    assert any(isinstance(m, HumanMessage) and m.content == "user reply" for m in result.output_messages)


# ---------------------------------------------------------------------------
# history_window 切片
# ---------------------------------------------------------------------------


def test_history_window_slices_messages():
    llm = FakeLLM([_mk_llm_response()])
    info = _node(transitions=[Transition(next="Finish")], history_window=1)
    messages = [HumanMessage(content=f"m{i}") for i in range(5)]
    state = AgentState(
        messages=messages,
        global_instructions="",
        state_instructions="",
        deliverables={},
        current_node="N",
        next_state="",
        loop_count=0,
        max_loops=10,
    )
    ctx = _ctx(info, llm, state=state)
    execute_llm(ctx)

    sent = llm.invoke_calls[0]
    # 第一条是 sys_prompt，之后应只剩 1 条 human 消息（窗口=1）
    assert len(sent) == 2
    assert sent[1].content == "m4"


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_no_tool_calls_final_node_no_payload():
    llm = FakeLLM([_mk_llm_response(content="final answer")])
    info = _node(is_final=True)
    ctx = _ctx(info, llm)
    result = execute_llm(ctx)

    assert result.next_state is None
    assert result.payload is None  # final 节点不产生 payload
