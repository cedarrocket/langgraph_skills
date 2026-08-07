"""子图（# [SubGraph]）机制测试。

覆盖：子图声明解析（形态 A / src 简写）、三态 transition（-> / ==> / ==>X<==）、
validator 的 -> 子图 warning、execute_skill 的覆盖/合并消息回传。
"""


import pytest
from langchain_core.messages import HumanMessage

from langgraph_skills.executors import ExecutorContext, execute_skill
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
