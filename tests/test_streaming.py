"""流式输出（on_token 回调）测试。

覆盖：_invoke_llm stream 聚合（token 回调 + 完整消息）、tool_calls 保留。
"""


from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from langgraph_skills.executors import ExecutorContext, _invoke_llm
from langgraph_skills.models import AgentState, NodeInfo
from langgraph_skills.tools import ToolRegistry


class FakeStreamLLM:
    """stream 模式：产出多个 chunk，invoke 模式：单条完整消息。"""

    def __init__(self, tokens):
        self.tokens = tokens

    def stream(self, messages):
        for t in self.tokens:
            yield AIMessageChunk(content=t)

    def invoke(self, messages):
        return AIMessage(content="".join(self.tokens))


def test_invoke_llm_invoke_without_callback():
    """无 on_token 回调时走 invoke。"""
    llm = FakeStreamLLM(["hello", " world"])
    ctx = ExecutorContext(
        node_info=NodeInfo(name="A"), state=AgentState(
            messages=[], global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node="A", next_state="", loop_count=0, max_loops=10,
        ),
        tools=ToolRegistry(), safe_input=lambda p: "", run_skill=lambda *a, **k: {},
    )
    result = _invoke_llm(llm, [HumanMessage(content="hi")], ctx)
    assert result.content == "hello world"


def test_invoke_llm_stream_with_callback():
    """on_token 回调时走 stream：逐 token 回调 + 聚合完整消息。"""
    tokens = []
    ctx = ExecutorContext(
        node_info=NodeInfo(name="A"), state=AgentState(
            messages=[], global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node="A", next_state="", loop_count=0, max_loops=10,
        ),
        tools=ToolRegistry(), safe_input=lambda p: "", run_skill=lambda *a, **k: {},
        on_token=lambda t: tokens.append(t),
    )
    llm = FakeStreamLLM(["你好", "世界"])
    result = _invoke_llm(llm, [HumanMessage(content="hi")], ctx)
    assert tokens == ["你好", "世界"]
    assert result.content == "你好世界"


def test_stream_tool_calls_preserved():
    """stream 聚合保留 tool_calls（工具调用场景）。

    模拟真实 stream：首个 chunk 带 name，后续 chunk name 为空、args 增量拼接。
    """
    chunks = [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "read_text", "args": "", "id": "c1", "index": 0, "type": "tool_call_chunk"}
        ]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "", "args": '{"path": "x"}', "id": "c1", "index": 0, "type": "tool_call_chunk"}
        ]),
    ]

    class ChunkLLM:
        def stream(self, messages):
            yield from chunks

        def invoke(self, messages):
            return AIMessage(content="", tool_calls=[{"name": "read_text", "args": {"path": "x"}, "id": "c1", "type": "tool_call"}])

    ctx = ExecutorContext(
        node_info=NodeInfo(name="A"), state=AgentState(
            messages=[], global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node="A", next_state="", loop_count=0, max_loops=10,
        ),
        tools=ToolRegistry(), safe_input=lambda p: "", run_skill=lambda *a, **k: {},
        on_token=lambda t: None,
    )
    result = _invoke_llm(ChunkLLM(), [HumanMessage(content="hi")], ctx)
    assert result.tool_calls
    assert result.tool_calls[0]["name"] == "read_text"
    assert result.tool_calls[0]["args"] == {"path": "x"}
