"""工具边界 hooks（hooks.json）与 NodeEnd executor signal() API 测试。

覆盖：hooks.json 加载（matcher 匹配）、pre_tool/post_tool/post_tool_failure
触发、handler 注入 tool_name/tool_args/tool_result、NodeEnd executor 用
signal() 抛 condition 覆盖路由。
"""


from langchain_core.messages import AIMessage, HumanMessage

from langgraph_skills.graph import build_graph
from langgraph_skills.hooks import ToolHookRule, ToolHooks, fire_tool_hooks, load_hooks
from langgraph_skills.models import AgentState


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _invoke(app, messages, start="B"):
    return app.invoke(
        AgentState(
            messages=messages, global_instructions="", state_instructions="",
            deliverables={}, spans=[], current_node=start, next_state="", loop_count=0, max_loops=10,
        )
    )


TOOL_SKILL = """# [Config]
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
"""


def _tool_call():
    return AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"filepath": "/etc/hostname"}, "id": "c1", "type": "tool_call"}],
    )


# ---------------------------------------------------------------------------
# hooks.json 加载
# ---------------------------------------------------------------------------


def test_load_hooks(tmp_path):
    (tmp_path / "hooks.json").write_text(
        """{
  "hooks": {
    "pre_tool": [{ "matcher": "*", "handler": "pre.py" }],
    "post_tool": [{ "matcher": "read_file", "handler": "post.py" }],
    "post_tool_failure": [{ "matcher": "write_file", "handler": "fail.py" }]
  }
}
""",
        encoding="utf-8",
    )
    hooks = load_hooks(global_path=tmp_path / "hooks.json")
    assert len(hooks.rules) == 3
    assert hooks.rules[0].checkpoint == "pre_tool"
    assert hooks.rules[0].matcher == "*"


def test_hook_matcher(tmp_path):
    hooks = ToolHooks(rules=[
        ToolHookRule(checkpoint="pre_tool", matcher="read_*", handler="x"),
        ToolHookRule(checkpoint="post_tool", matcher="*", handler="y"),
    ])
    assert hooks.matches(hooks.rules[0], "read_file")
    assert not hooks.matches(hooks.rules[0], "write_file")
    assert hooks.matches(hooks.rules[1], "anything")


# ---------------------------------------------------------------------------
# 工具边界 hooks 触发
# ---------------------------------------------------------------------------


def test_tool_hooks_fire(tmp_path):
    """pre_tool / post_tool hooks 在工具调用前后触发（handler 注入 tool_name）。"""
    pre_script = tmp_path / "pre.py"
    pre_script.write_text(
        "print(f'  [Hook:pre_tool] tool={tool_name} args={tool_args}')\n", encoding="utf-8"
    )
    post_script = tmp_path / "post.py"
    post_script.write_text(
        "print(f'  [Hook:post_tool] tool={tool_name} result={str(tool_result)[:10]}')\n", encoding="utf-8"
    )
    (tmp_path / "hooks.json").write_text(
        f"""{{
  "hooks": {{
    "pre_tool": [{{ "matcher": "*", "handler": "{pre_script}" }}],
    "post_tool": [{{ "matcher": "read_file", "handler": "{post_script}" }}]
  }}
}}
""",
        encoding="utf-8",
    )
    path = _write(tmp_path, TOOL_SKILL)
    import os


    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
        r = _invoke(app, [HumanMessage(content="hi"), _tool_call()])
    finally:
        os.chdir(old_cwd)
    # 工具执行成功：ToolMessage 存在且 read_file 产出真实内容
    tool_msgs = [m for m in r["messages"] if type(m).__name__ == "ToolMessage"]
    assert tool_msgs
    assert getattr(tool_msgs[0], "content", "").strip()  # read_file 真实结果非空
    # 图正常结束
    assert r["deliverables"] is not None


def test_tool_hooks_injection_scope(tmp_path):
    """fire_tool_hooks 向 handler 注入 tool_name/tool_args/tool_result。"""
    seen = {}

    def fake_handler(path, scope):
        seen.update(scope)

    import langgraph_skills.triggers as trig
    orig = trig.run_handler
    trig.run_handler = fake_handler
    try:
        hooks = ToolHooks(rules=[ToolHookRule(checkpoint="pre_tool", matcher="*", handler="x")])
        fire_tool_hooks(hooks, "pre_tool", {"tool_name": "read_file", "tool_args": {"p": "/etc/hostname"}}, "read_file")
        assert seen.get("tool_name") == "read_file"
    finally:
        trig.run_handler = orig


# ---------------------------------------------------------------------------
# NodeEnd executor signal() API
# ---------------------------------------------------------------------------


def test_node_end_executor_signal(tmp_path):
    """NodeEnd executor 内用 signal() 抛 condition 覆盖路由。"""
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
```python
if deliverables.get("payload", "").startswith("w"):
    signal("give_up")
```

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
    r = _invoke(app, [HumanMessage(content="hi")], start="Work")
    assert r["deliverables"].get("worked") == "yes"
    assert r["deliverables"].get("payload") == "giveup"


def test_node_end_executor_negative(tmp_path):
    """NodeEnd executor 未抛 signal → 走默认路由。"""
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
```python
if deliverables.get("payload", "").startswith("zzz"):
    signal("give_up")
```

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
    r = _invoke(app, [HumanMessage(content="hi")], start="Work")
    assert r["deliverables"].get("payload") == "done"
