"""子图（# [SubGraph]）机制测试。

覆盖：子图声明解析（形态 A / src 简写）、三态 transition（-> / ==> / ==>X<==）、
validator 的 -> 子图 warning、execute_skill 的覆盖/合并消息回传。
"""


import pytest
from langchain_core.messages import HumanMessage

from langgraph_skills.executors import ExecutorContext, execute_skill
from langgraph_skills.graph import build_graph
from langgraph_skills.models import AgentState, NodeInfo, Transition
from langgraph_skills.parser import ParseError, parse_compiled_skill, validate_node_graph
from langgraph_skills.tools import ToolRegistry


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 子图声明解析
# ---------------------------------------------------------------------------


def test_subgraph_parse_nodes(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] Analyze
- **is_final**: false

## [Transitions]
- Default ==> CompressContext <==

# [SubGraph] CompressContext
## [Node] Summarize
- **type**: llm
- 摘要旧消息

## [Node] Trim
- **type**: code

```python
compact(5)
```
""",
    )
    compiled = parse_compiled_skill(path)
    assert "CompressContext" in compiled.subgraphs
    sub = compiled.subgraphs["CompressContext"]
    assert list(sub.nodes.keys()) == ["Summarize", "Trim"]
    assert sub.nodes["Trim"].node_type == "code"


def test_subgraph_src_shorthand(tmp_path):
    path = _write(
        tmp_path,
        """# [SubGraph] External
- **src**: external_skill.md
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.subgraphs["External"].src == "external_skill.md"


def test_subgraph_duplicate_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [SubGraph] A
- **src**: x.md

# [SubGraph] A
- **src**: y.md
""",
    )
    with pytest.raises(ParseError, match="Duplicate subgraph"):
        parse_compiled_skill(path)


# ---------------------------------------------------------------------------
# 三态 transition
# ---------------------------------------------------------------------------


def test_transition_three_forms(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: false

## [Transitions]
- Default ==> Replace <==
- Default ==> Merge
- Default -> Plain

# [SubGraph] Replace
- **src**: r.md

# [SubGraph] Merge
- **src**: m.md

# [Node] Plain
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    ts = compiled.nodes["A"].transitions
    by_next = {t.next: t for t in ts}
    assert by_next["Replace"].inherit_history is True
    assert by_next["Replace"].replace_messages is True
    assert by_next["Merge"].inherit_history is True
    assert by_next["Merge"].replace_messages is False
    assert by_next["Plain"].inherit_history is False
    assert by_next["Plain"].replace_messages is False


def test_table_transition_replace_form(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: false

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| done | ==> Compress <== | no | |

# [SubGraph] Compress
- **src**: c.md
""",
    )
    compiled = parse_compiled_skill(path)
    t = compiled.nodes["A"].transitions[0]
    assert t.next == "Compress"
    assert t.inherit_history is True
    assert t.replace_messages is True


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------


def test_subgraph_target_not_dangling(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: false

## [Transitions]
- Default ==> Compress

# [SubGraph] Compress
- **src**: c.md
""",
    )
    compiled = parse_compiled_skill(path)
    errors = validate_node_graph(compiled.nodes, subgraph_names=set(compiled.subgraphs.keys()))
    assert errors == []  # 指向子图不算悬空


def test_subgraph_without_inherit_warns(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: false

## [Transitions]
- Default -> Compress

# [SubGraph] Compress
- **src**: c.md
""",
    )
    compiled = parse_compiled_skill(path)
    assert any("without `==>`" in w for w in compiled.warnings)


# ---------------------------------------------------------------------------
# execute_skill：覆盖/合并消息回传
# ---------------------------------------------------------------------------


def _skill_ctx(state, transition, tmp_path=None):
    src = "child.md"
    if tmp_path is not None:
        p = tmp_path / "child.md"
        p.write_text("# [Node] C\n- **is_final**: true\n", encoding="utf-8")
        src = str(p)
    info = NodeInfo(name="Child", node_type="skill", src=src, transitions=[transition])
    return ExecutorContext(
        node_info=info,
        state=state,
        tools=ToolRegistry(),
        safe_input=lambda p: "",
        run_skill=lambda *a, **k: {"payload": "child-out", "messages": [HumanMessage(content="child-msg")]},
    )


def test_execute_skill_replace_messages(tmp_path):
    state = AgentState(
        messages=[HumanMessage(content="parent-1"), HumanMessage(content="parent-2")],
        global_instructions="", state_instructions="", deliverables={},
        current_node="A", next_state="", loop_count=0, max_loops=10,
    )
    ctx = _skill_ctx(state, Transition(next="Child", inherit_history=True, replace_messages=True), tmp_path)
    result = execute_skill(ctx)
    assert result.payload == "child-out"
    # 覆盖：父图 messages 被替换为子图返回的
    assert [m.content for m in state["messages"]] == ["child-msg"]


def test_execute_skill_merge_messages(tmp_path):
    state = AgentState(
        messages=[HumanMessage(content="parent-1", id="p1")],
        global_instructions="", state_instructions="", deliverables={},
        current_node="A", next_state="", loop_count=0, max_loops=10,
    )
    # 子图返回：同 ID 的父消息 + 新消息
    child_msgs = [
        HumanMessage(content="parent-1", id="p1"),
        HumanMessage(content="child-new", id="c1"),
    ]
    info = NodeInfo(
        name="Child", node_type="skill",
        src=str(tmp_path / "child.md") if tmp_path else "child.md",
        transitions=[Transition(next="Child", inherit_history=True)],
    )
    p = tmp_path / "child.md"
    p.write_text("# [Node] C\n- **is_final**: true\n", encoding="utf-8")
    ctx = ExecutorContext(
        node_info=info, state=state, tools=ToolRegistry(), safe_input=lambda p: "",
        run_skill=lambda *a, **k: {"payload": "x", "messages": child_msgs},
    )
    execute_skill(ctx)
    # 合并：父消息保留 + 子图新消息追加（按 ID 去重）
    contents = [m.content for m in state["messages"]]
    assert "parent-1" in contents
    assert "child-new" in contents


# ---------------------------------------------------------------------------
# 真子图（LangGraph add_node 子图）
# ---------------------------------------------------------------------------


def _invoke(app, messages, payload="", start="A"):
    return app.invoke(
        AgentState(
            messages=messages, global_instructions="", state_instructions="",
            deliverables={} if not payload else {"payload": payload},
            current_node=start, next_state="", loop_count=0, max_loops=10,
        )
    )


def test_true_subgraph_runs_nodes(tmp_path):
    """真子图：# [SubGraph] 编译为 add_node 子图，内部节点执行并回传。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("Sub", "x")
```

## [Transitions]
- Default ==> Sub

# [SubGraph] Sub
## [Node] S1
- **type**: code

```python
transition_to(None, "s1done")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["deliverables"]["payload"] == "s1done"


def test_true_subgraph_inherits_messages(tmp_path):
    """真子图 ==> 继承：子图内部可见父图 messages。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("Sub", "x")
```

## [Transitions]
- Default ==> Sub

# [SubGraph] Sub
## [Node] S1
- **type**: code

```python
transition_to(None, "saw-" + str(len(messages)))
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="m1"), HumanMessage(content="m2")])
    assert r["deliverables"]["payload"] == "saw-2"


def test_true_subgraph_replace_messages(tmp_path):
    """真子图 ==> X <== 覆盖：子图经 _child_messages 协议整体替换父图 messages。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("Sub", "x")
```

## [Transitions]
- Default ==> Sub <==

# [SubGraph] Sub
## [Node] S1
- **type**: code

```python
deliverables["_child_messages"] = messages[-1:]
transition_to(None, "compressed")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="m1"), HumanMessage(content="m2"), HumanMessage(content="m3")])
    assert [m.content for m in r["messages"]] == ["m3"]


def test_true_subgraph_pre_node_redirect(tmp_path):
    """pre_node 超限 → 提前 return 跳真子图 → 覆盖回传（不计 loop）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("B", "x")
```

## [Transitions]
- Default -> B

# [Node] B
- **type**: code
- **max_context_length**: 10

```python
transition_to(None, "B-run")
```

## [Transitions]
- Default ==> Compress <==

# [SubGraph] Compress
## [Node] C1
- **type**: code

```python
deliverables["_child_messages"] = messages[-1:]
transition_to(None, "compressed")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    msgs = [HumanMessage(content=f"msg {i}" + "x" * 50) for i in range(5)]
    r = _invoke(app, msgs)
    # 覆盖回传：压缩到 1 条
    assert len(r["messages"]) == 1
    assert r["deliverables"]["payload"] == "compressed"
    # B 被跳过（未执行），loop 计数不含 B：A(1) + 子图(2)
    assert r["loop_count"] == 2


def test_true_subgraph_nested(tmp_path):
    """递归嵌套：子图内部再声明子图。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("Sub1", "x")
```

## [Transitions]
- Default -> Sub1

# [SubGraph] Sub1
## [Node] S1
- **type**: code

```python
transition_to("Sub2", "x")
```

## [Transitions]
- Default -> Sub2

## [SubGraph] Sub2
### [Node] S2
- **type**: code

```python
transition_to(None, "nested-done")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["deliverables"]["payload"] == "nested-done"


def test_subgraph_multi_node_transitions_owned(tmp_path):
    """子图内多节点的 ## [Transitions] 必须归属各自节点（防错位）。

    回归：## [Transitions] 曾被 _split_sub_sections 顶层切分，导致
    transitions 全部错位（Parse→RetryFix 而非 Report 等）。
    """
    path = _write(
        tmp_path,
        """# [Node] Start
- **type**: code
- **is_final**: true

# [SubGraph] Sub
## [Node] Parse
- **type**: code

## [Transitions]
- Default -> Report

## [Node] RetryFix
- **type**: llm

## [Transitions]
- Default -> Parse

## [Node] Report
- **type**: code
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    sub = c.subgraphs["Sub"]
    assert [t.next for t in sub.nodes["Parse"].transitions] == ["Report"]
    assert [t.next for t in sub.nodes["RetryFix"].transitions] == ["Parse"]
    assert sub.nodes["Report"].transitions == []


def test_subgraph_retry_loop_runs(tmp_path):
    """子图内 出错->RetryFix->回Parse 重试循环（确定性 code 版本）。"""
    path = _write(
        tmp_path,
        """# [Node] Start
- **type**: code

```python
deliverables["tool_attempts"] = 0
deliverables["payload"] = "bad"
transition_to("Sub", "go")
```

## [Transitions]
- Default ==> Sub <==

# [SubGraph] Sub
## [Node] Parse
- **type**: code

```python
attempts = deliverables.get("tool_attempts", 0)
deliverables["tool_attempts"] = attempts
if attempts == 0:
    deliverables["tool_result"] = "Error: first attempt"
    transition_to("RetryFix", "Error: first attempt")
else:
    deliverables["tool_result"] = "OK after retry"
    transition_to(None, "ok")
```

## [Transitions]
- Default -> Report

## [Node] RetryFix
- **type**: code

```python
deliverables["tool_attempts"] = deliverables.get("tool_attempts", 0) + 1
transition_to("Parse", "retry")
```

## [Transitions]
- Default -> Parse

## [Node] Report
- **type**: code
- **is_final**: true

```python
transition_to(None, deliverables.get("tool_result", ""))
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")], start="Start")
    assert r["deliverables"]["tool_result"] == "OK after retry"
