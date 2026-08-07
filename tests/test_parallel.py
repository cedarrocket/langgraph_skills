"""静态 fan-out / fan-in（并行分支 + 收敛合并）机制测试。

覆盖：Parallel 前缀解析（列表/表格）、多目标拆分、fan-in 合并
（deliverables 字段级 + next_state/loop_count 单值不冲突）、
validator 收敛检查（防死等）、并行执行正确性。
"""


import time

from langchain_core.messages import HumanMessage

from langgraph_skills.graph import build_graph
from langgraph_skills.models import AgentState
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _invoke(app, messages, start="FanOut"):
    return app.invoke(
        AgentState(
            messages=messages, global_instructions="", state_instructions="",
            deliverables={}, current_node=start, next_state="", loop_count=0, max_loops=10,
        )
    )


FANOUT_SKILL = """# [Node] FanOut
- **type**: code

```python
transition_to(["B", "C"], "x")
```

## [Transitions]
- Parallel ==> B, C

# [Node] B
- **type**: code

```python
deliverables["b_result"] = "B-产出"
transition_to("Join", "b")
```

## [Transitions]
- Default -> Join

# [Node] C
- **type**: code

```python
deliverables["c_result"] = "C-产出"
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
"""


def test_parallel_parse_list(tmp_path):
    """Parallel 前缀 + 逗号分隔 → 多个 parallel=True 的 Transition。"""
    path = _write(tmp_path, FANOUT_SKILL)
    c = parse_compiled_skill(path)
    fanout = c.nodes["FanOut"]
    targets = [(t.next, t.parallel) for t in fanout.transitions]
    assert ("B", True) in targets
    assert ("C", True) in targets


def test_parallel_parse_table(tmp_path):
    """表格形式 Parallel 同样支持。"""
    path = _write(
        tmp_path,
        """# [Node] FanOut
- **type**: code

## [Transitions]
| Condition | Next Node |
| --- | --- |
| Parallel | B, C |

# [Node] B
- **is_final**: true

# [Node] C
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    targets = [(t.next, t.parallel) for t in c.nodes["FanOut"].transitions]
    assert ("B", True) in targets
    assert ("C", True) in targets


def test_parallel_semicolon_separator(tmp_path):
    """分号分隔同样支持。"""
    path = _write(
        tmp_path,
        """# [Node] FanOut
- **type**: code

## [Transitions]
- Parallel ==> B; C

# [Node] B
- **is_final**: true

# [Node] C
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    targets = [t.next for t in c.nodes["FanOut"].transitions]
    assert "B" in targets and "C" in targets


def test_fanout_fanin_executes_join_once(tmp_path):
    """fan-out 并行执行，Join 只触发一次且看到全部合并产出。"""
    path = _write(tmp_path, FANOUT_SKILL)
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["deliverables"]["payload"] == "joined"
    assert r["deliverables"]["b_result"] == "B-产出"
    assert r["deliverables"]["c_result"] == "C-产出"


def test_fanout_parallelism_speed(tmp_path):
    """并行确实并行：两个 0.3s 的分支耗时 ~0.3s 而非 0.6s。"""
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
import time; time.sleep(0.3)
transition_to("Join", "b")
```

## [Transitions]
- Default -> Join

# [Node] C
- **type**: code

```python
import time; time.sleep(0.3)
transition_to("Join", "c")
```

## [Transitions]
- Default -> Join

# [Node] Join
- **is_final**: true
- **type**: code

```python
transition_to(None, "joined")
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    t0 = time.time()
    _invoke(app, [HumanMessage(content="hi")])
    elapsed = time.time() - t0
    # 串行 0.6s，并行 ~0.3s；容忍调度开销给 0.55s 上界
    assert elapsed < 0.55, f"fan-out 未并行，耗时 {elapsed:.2f}s"


def test_fanout_deadlock_detected(tmp_path):
    """validator 拒绝不收敛的 fan-out（分支不到共同 join → 死等）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

## [Transitions]
- Parallel ==> B, C

# [Node] B
## [Transitions]
- Default -> Join

# [Node] C
- **is_final**: true

# [Node] Join
- **is_final**: true
""",
    )
    c = parse_compiled_skill(path)
    errs = validate_node_graph(c.nodes)
    assert any("never converge" in e for e in errs)


def test_fanout_nested_chain_join(tmp_path):
    """链式分支（A→B→C 与 A→D）：Join 等待链尾 C、D。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to(["B", "D"], "x")
```

## [Transitions]
- Parallel ==> B, D

# [Node] B
- **type**: code

```python
deliverables["b_chain"] = "B→C"
transition_to("C", "x")
```

## [Transitions]
- Default -> C

# [Node] C
- **type**: code

```python
deliverables["c_chain"] = "C 产出"
transition_to("Join", "x")
```

## [Transitions]
- Default -> Join

# [Node] D
- **type**: code

```python
deliverables["d_chain"] = "D 产出"
transition_to("Join", "x")
```

## [Transitions]
- Default -> Join

# [Node] Join
- **is_final**: true
- **type**: code

```python
transition_to(None, deliverables.get("c_chain", "") + "+" + deliverables.get("d_chain", ""))
```
""",
    )
    app = build_graph(path, safe_input=lambda p: "", run_skill=lambda *a, **k: {})
    r = _invoke(app, [HumanMessage(content="hi")])
    assert r["deliverables"]["payload"] == "C 产出+D 产出"
    assert r["deliverables"]["b_chain"] == "B→C"
