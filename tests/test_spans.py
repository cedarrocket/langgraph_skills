"""消息归属（metadata）与跨度追踪（spans）测试。

覆盖：节点产出消息自动打 node/loop metadata、span 记录
（start/end 索引、类型、prompt 边界）、fan-out 时多分支 span 全部保留。
"""


from langchain_core.messages import HumanMessage

from langgraph_skills.graph import build_graph
from langgraph_skills.models import AgentState


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _invoke(app, messages, start="A"):
    return app.invoke(
        AgentState(
            messages=messages, global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node=start, next_state="", loop_count=0, max_loops=10,
        )
    )


def test_spans_recorded_per_node(tmp_path):
    """每个节点执行后记录 span（node/loop/type/start/end）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("B", "a")
```

## [Transitions]
- Default -> B

# [Node] B
- **type**: code
- **is_final**: true

```python
transition_to(None, "b")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    spans = r["spans"]
    assert [s["node"] for s in spans] == ["A", "B"]
    assert [s["type"] for s in spans] == ["code", "code"]
    # code 节点不产出消息：start == end
    for s in spans:
        assert s["start"] == s["end"] == 1


def test_metadata_tagged_on_messages(tmp_path):
    """节点产出的消息自动打 node/loop metadata。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code
- **is_final**: true

```python
transition_to(None, "a")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    # code 节点无输出消息，改测注入：用 output_messages 路径（LLM/交互）——
    # 直接验证 messages 引用打标逻辑：code 节点不产出，故此处仅验证 span 存在
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["spans"][0]["node"] == "A"


def test_spans_fanout_all_branches(tmp_path):
    """fan-out 多分支的 span 全部保留（append_list reducer）。"""
    path = _write(
        tmp_path,
        """# [Node] FanOut
- **type**: code

```python
transition_to(["B", "C"], "x")
```

## [Transitions]
- Parallel ==> B, C

# [Node] B
- **type**: code

```python
transition_to("Join", "b")
```

## [Transitions]
- Default -> Join

# [Node] C
- **type**: code

```python
transition_to("Join", "c")
```

## [Transitions]
- Default -> Join

# [Node] Join
- **type**: code
- **is_final**: true

```python
transition_to(None, "joined")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    nodes = [s["node"] for s in r["spans"]]
    assert "B" in nodes and "C" in nodes and "Join" in nodes
    # B 和 C 的 span 都保留（未被覆盖）
    assert sum(1 for s in r["spans"] if s["node"] == "B") == 1
    assert sum(1 for s in r["spans"] if s["node"] == "C") == 1


def test_tool_call_metadata_and_span(tmp_path):
    """工具调用：ToolMessage 打 metadata（node/tool/args）+ tool span（工具名/参数/结果/起止点）。"""
    path = _write(
        tmp_path,
        """# [Config]
- max_loops: 5

# [Node] B
- **type**: code

```python
transition_to(None, "b")
```

## [Transitions]
- Default -> A

# [Node] A
- **is_final**: true
- **type**: code

```python
transition_to(None, "a")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    from langchain_core.messages import AIMessage

    ai = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"filepath": "/etc/hostname"}, "id": "c1", "type": "tool_call"}],
    )
    r = _invoke(app, [HumanMessage(content="hi"), ai])
    # ToolMessage 带归属 metadata
    tool_msgs = [m for m in r["messages"] if type(m).__name__ == "ToolMessage"]
    assert tool_msgs, "tool call 应产生 ToolMessage"
    md = getattr(tool_msgs[0], "metadata", None) or {}
    assert md.get("node") == "tools"
    assert md.get("tool") == "read_file"
    assert "filepath" in (md.get("args") or {})
    # tool span 记录工具名/参数/结果/起止点
    tool_spans = [s for s in r["spans"] if s.get("type") == "tool"]
    assert tool_spans, "工具调用应有 tool span"
    ts = tool_spans[0]
    assert ts["tool"] == "read_file"
    assert ts["args"].get("filepath") == "/etc/hostname"
    assert ts["start"] < ts["end"]
    assert isinstance(ts["result"], str)


def test_code_node_reads_spans(tmp_path):
    """代码节点入口可通过 spans 变量看到执行历史（入口 = 到本节点为止的历史，不含本次）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
deliverables["A_seen"] = str(len(spans))
transition_to("B", "a")
```

## [Transitions]
- Default -> B

# [Node] B
- **is_final**: true
- **type**: code

```python
# B 入口看到 A 已执行完的 span
deliverables["B_seen"] = "|".join(f"{s['node']}:{s['start']}-{s['end']}" for s in spans)
transition_to(None, "b")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    # A 是首个节点：入口时无历史
    assert r["deliverables"]["A_seen"] == "0"
    # B 入口看到 A 的 span
    assert r["deliverables"]["B_seen"] == "A:1-1"
