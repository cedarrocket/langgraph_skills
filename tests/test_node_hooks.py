"""节点钩子（## [NodeStart] / ## [NodeEnd]）机制测试。

覆盖：NodeStart/NodeEnd 解析（context/on:/executor）、context 三模式
（all/previous_payload/executor）、on: 三形态检测器（内置谓词/pyfunction/
trigger）、signal 与 Transitions 对接、NodeStart 跳过执行、NodeEnd 覆盖路由、
executor 空产出报错、NodeEnd 拒绝 context。
"""


import pytest
from langchain_core.messages import AIMessage, HumanMessage

from langgraph_skills.graph import build_graph
from langgraph_skills.models import AgentState, OnCondition
from langgraph_skills.parser import ParseError, parse_compiled_skill
from langgraph_skills.tools import ToolRegistry


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _invoke(app, messages, start="Analyze", loop_count=0):
    return app.invoke(
        AgentState(
            messages=messages, global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node=start, next_state="",
            loop_count=loop_count, max_loops=10,
        )
    )


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_node_start_parse(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm

## [NodeStart]
- **context**: previous_payload
- **on**: context_length_exceeded(5000) :=> compact
- **on**: pyfunction: ./check.py :=> needs_fix

## [Transitions]
- Default -> Done
""",
    )
    c = parse_compiled_skill(path)
    ns = c.nodes["A"].node_start
    assert ns.context == "previous_payload"
    assert [(x.kind, x.arg, x.signal) for x in ns.conditions] == [
        ("context_length_exceeded", 5000, "compact"),
        ("pyfunction", "./check.py", "needs_fix"),
    ]


def test_node_end_parse(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm

## [Transitions]
- Default -> Done

## [NodeEnd]
- **on**: loop_count_exceeded(3) :=> give_up
""",
    )
    c = parse_compiled_skill(path)
    ne = c.nodes["A"].node_end
    assert [(x.kind, x.arg, x.signal) for x in ne.conditions] == [
        ("loop_count_exceeded", 3, "give_up")
    ]


def test_node_end_rejects_context(tmp_path):
    """NodeEnd 不允许 context（不得触碰上下文输入）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm

## [Transitions]
- Default -> Done

## [NodeEnd]
- **context**: all
""",
    )
    with pytest.raises(ParseError):
        parse_compiled_skill(path)


def test_unknown_predicate_rejected(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm

## [NodeStart]
- **on**: foo(1) :=> x

## [Transitions]
- Default -> Done
""",
    )
    with pytest.raises(ParseError):
        parse_compiled_skill(path)


# ---------------------------------------------------------------------------
# NodeStart 跳过执行 + signal 对接
# ---------------------------------------------------------------------------


def test_node_start_signal_skips_execution(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] Analyze
- **type**: code

```python
deliverables["ran"] = "yes"
transition_to("Done", "x")
```

## [NodeStart]
- **on**: context_length_exceeded(5) :=> compact

## [Transitions]
| Condition | Next Node |
| compact   | Compact   |
| Default   | Done      |

# [Node] Compact
- **type**: code
- **is_final**: true

```python
deliverables["compacted"] = "yes"
transition_to(None, "c")
```

# [Node] Done
- **type**: code
- **is_final**: true

```python
transition_to(None, "done")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})

    # 短上下文：正常执行 Analyze
    r1 = _invoke(app, [HumanMessage(content="hi")])
    assert r1["deliverables"].get("ran") == "yes"
    assert r1["deliverables"].get("payload") == "done"

    # 长上下文：NodeStart 抛 compact，跳过 Analyze，直接到 Compact
    r2 = _invoke(app, [HumanMessage(content="x" * 50)])
    assert r2["deliverables"].get("ran") is None
    assert r2["deliverables"].get("compacted") == "yes"


def test_node_start_pyfunction_condition(tmp_path):
    """pyfunction 检测器：外部脚本返回 True 即触发。"""
    script = tmp_path / "check.py"
    script.write_text("trigger_result(True)\n", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""# [Node] Analyze
- **type**: code

```python
deliverables["ran"] = "yes"
transition_to("Done", "x")
```

## [NodeStart]
- **on**: pyfunction: {script} :=> compact

## [Transitions]
| Condition | Next Node |
| compact   | Compact   |
| Default   | Done      |

# [Node] Compact
- **type**: code
- **is_final**: true

```python
transition_to(None, "c")
```

# [Node] Done
- **type**: code
- **is_final**: true

```python
transition_to(None, "done")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["deliverables"].get("ran") is None
    assert r["deliverables"].get("payload") == "c"


# ---------------------------------------------------------------------------
# NodeEnd 覆盖路由
# ---------------------------------------------------------------------------


def test_node_end_signal_overrides_route(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] Work
- **type**: code

```python
deliverables["worked"] = "yes"
transition_to("Done", "w")
```

## [Transitions]
| Condition  | Next Node |
| give_up    | GiveUp    |
| Default    | Done      |

## [NodeEnd]
- **on**: loop_count_exceeded(2) :=> give_up

# [Node] GiveUp
- **type**: code
- **is_final**: true

```python
transition_to(None, "giveup")
```

# [Node] Done
- **type**: code
- **is_final**: true

```python
transition_to(None, "done")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    # loop_count=3（超2）→ NodeEnd 覆盖到 GiveUp
    r = _invoke(app, [HumanMessage(content="hi")], start="Work", loop_count=3)
    assert r["deliverables"].get("worked") == "yes"
    assert r["deliverables"].get("payload") == "giveup"


# ---------------------------------------------------------------------------
# context 模式
# ---------------------------------------------------------------------------


def test_context_previous_payload(tmp_path):
    """previous_payload：只继承上一节点最终 payload，不含历史消息。"""
    path = _write(
        tmp_path,
        """# [Node] B
- **type**: llm

任务。

## [NodeStart]
- **context**: previous_payload

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    assert c.nodes["B"].node_start.context == "previous_payload"


def test_context_executor_requires_nonempty(tmp_path):
    """context: executor 空产出 → 报错。"""
    path = _write(
        tmp_path,
        """# [Node] B
- **type**: llm

任务。

## [NodeStart]
- **context**: executor

```python
ctx_messages = []
```

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    with pytest.raises(Exception):
        _invoke(app, [HumanMessage(content="hi")], start="B")


def test_context_executor_missing_code_rejected(tmp_path):
    """context: executor 但无 executor 代码 → 解析报错。"""
    path = _write(
        tmp_path,
        """# [Node] B
- **type**: llm

任务。

## [NodeStart]
- **context**: executor

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
""",
    )
    with pytest.raises(ParseError):
        parse_compiled_skill(path)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def test_eval_predicates():
    """内置谓词求值。"""
    from langgraph_skills.nodes import _condition_scope, _eval_on_condition

    state = AgentState(
        messages=[HumanMessage(content="x" * 10)], global_instructions="", state_instructions="",
        deliverables={}, spans=[], current_node="A", next_state="", loop_count=0, max_loops=10,
    )
    scope = _condition_scope(AIMessage(content=""), state)
    # context_length = 10
    assert _eval_on_condition(OnCondition(kind="context_length_exceeded", arg=5, signal="x"), scope)
    assert not _eval_on_condition(OnCondition(kind="context_length_exceeded", arg=20, signal="x"), scope)
    # loop_count = 0
    assert not _eval_on_condition(OnCondition(kind="loop_count_exceeded", arg=3, signal="x"), scope)


def test_resolve_signal_target(tmp_path):
    """signal 名与 Transitions Condition 列对接。"""
    from langgraph_skills.nodes import _resolve_signal_target

    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm

## [Transitions]
| Condition | Next Node |
| compact   | Compact   |
| Default   | Done      |

# [Node] Compact
- **is_final**: true

# [Node] Done
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    info = c.nodes["A"]
    assert _resolve_signal_target(info, "compact") == "Compact"
    assert _resolve_signal_target(info, "nope") is None


def test_context_all_ignores_cursor(tmp_path):
    """context: all 应看到全部历史（忽略 start_msg_index 游标）。"""
    from langgraph_skills.executors import ExecutorContext

    path = _write(
        tmp_path,
        """# [Node] B
- **type**: llm

检查。

## [NodeStart]
- **context**: all

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    info = c.nodes["B"]

    state = AgentState(
        messages=[
            HumanMessage(content="早期消息"),
            AIMessage(content="工具结果"),
            HumanMessage(content="新输入"),
        ],
        global_instructions="g", state_instructions="",
        deliverables={"start_msg_index": 2},  # 游标指向最后，all 应忽略
        spans=[], current_node="Input", next_state="", loop_count=0, max_loops=10,
    )
    ctx = ExecutorContext(
        node_info=info, state=state, tools=ToolRegistry(),
        safe_input=lambda p: "", run_skill=lambda *a, **k: {},
        settings=type("S", (), {"api_key": None})(),
    )
    seen = []

    class FakeLLM:
        def bind_tools(self, *a, **k):
            return self

        def invoke(self, msgs):
            seen.extend(str(m.content) for m in msgs)
            return AIMessage(content="ok")

    import langgraph_skills.executors as ex_mod

    orig = ex_mod.ChatOpenAI
    ex_mod.ChatOpenAI = lambda *a, **k: FakeLLM()
    try:
        ex_mod.execute_llm(ctx)
    finally:
        ex_mod.ChatOpenAI = orig
    # all 模式应看到早期消息 + 工具结果（忽略游标 start_msg_index=2）
    assert any("早期消息" in s for s in seen)
    assert any("工具结果" in s for s in seen)
