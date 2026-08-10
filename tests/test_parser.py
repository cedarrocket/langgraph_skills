import pytest

from langgraph_skills.parser import ParseError, parse_compiled_skill, validate_node_graph


def _write(tmp_path, content, name="skill.md"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_unknown_section_is_warning(tmp_path):
    path = _write(
        tmp_path,
        """# [Config]
- **max_loops**: 5

# [NotASection] hello
some natural language

# [Node] Start
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert any("Unknown section" in w for w in compiled.warnings)
    assert "Start" in compiled.nodes


def test_unknown_section_strict_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [Config]
- **max_loops**: 5

# [NotASection] hello

# [Node] Start
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="Unknown section"):
        parse_compiled_skill(path, strict=True)


def test_duplicate_node_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] Start
- **is_final**: true

# [Node] Start
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="Duplicate state names"):
        parse_compiled_skill(path)


def test_sequential_fallback(tmp_path):
    # 非 final 且无 transitions 的状态，自动连接声明顺序的下一个
    path = _write(
        tmp_path,
        """# [Node] First

do first

# [Node] Second

do second

# [Node] Final
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.nodes["First"].transitions[0].next == "Second"
    assert compiled.nodes["Second"].transitions[0].next == "Final"
    assert compiled.nodes["Final"].transitions == []


def test_io_reader_writer_generates_reserved_options(tmp_path):
    path = _write(
        tmp_path,
        """# [IO]
- **reader**: txt_reader
- **writer**: txt_writer

# [Node] Start
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    names = [o.name for o in compiled.input_options]
    assert "input_path" in names
    assert "output_path" in names
    input_opt = next(o for o in compiled.input_options if o.name == "input_path")
    output_opt = next(o for o in compiled.input_options if o.name == "output_path")
    assert input_opt.reader == "txt_reader"
    assert output_opt.writer == "txt_writer"


def test_node_max_loops_inherits_global_default(tmp_path):
    path = _write(
        tmp_path,
        """# [Config]
- **max_loops**: 15

# [Node] Start
- **max_loops**: 3

# [Node] Finish
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.max_loops == 15
    assert compiled.nodes["Start"].max_loops == 3
    assert compiled.nodes["Finish"].max_loops is None


def test_node_missing_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [Node]

# [Node] Finish
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="missing name"):
        parse_compiled_skill(path)


# ---------------------------------------------------------------------------
# ==> 消息继承语法
# ---------------------------------------------------------------------------


def test_list_transition_inherit_history(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: true

## [Transitions]
- Default ==> Finish

# [Node] Finish
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    t = compiled.nodes["A"].transitions[0]
    assert t.next == "Finish"
    assert t.inherit_history is True


def test_list_transition_no_inherit_default(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: true

## [Transitions]
- Default -> Finish

# [Node] Finish
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    t = compiled.nodes["A"].transitions[0]
    assert t.next == "Finish"
    assert t.inherit_history is False


def test_table_transition_inherit_history_prefix(tmp_path):
    path = _write(
        tmp_path,
        """# [Node] A
- **is_final**: true

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| done | ==> Fix | no | |
| unclear | Reask | no | |

# [Node] Fix
- **is_final**: true

# [Node] Reask
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    t_inherit = next(t for t in compiled.nodes["A"].transitions if t.next == "Fix")
    t_normal = next(t for t in compiled.nodes["A"].transitions if t.next == "Reask")
    assert t_inherit.inherit_history is True
    assert t_normal.inherit_history is False


def test_tools_metadata_bracket_and_plain(tmp_path):
    """tools 元数据支持 `[a, b]`（带方括号）与 `a, b`（无括号）两种写法。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: llm
- **tools**: [list_dir, read_text, write_text, append_text]
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.nodes["A"].tools == ["list_dir", "read_text", "write_text", "append_text"]

    path2 = _write(
        tmp_path,
        """# [Node] B
- **type**: llm
- **tools**: read_file, write_file
""",
        name="skill2.md",
    )
    compiled2 = parse_compiled_skill(path2)
    assert compiled2.nodes["B"].tools == ["read_file", "write_file"]


def test_main_graph_transition_to_must_be_declared(tmp_path):
    """主图 code 节点 transition_to 目标必须在 transitions 表声明（防漂移）。"""
    path = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("B", "x")
```

## [Transitions]
- Default -> C

# [Node] C
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    errors = validate_node_graph(compiled.nodes)
    assert any("not declared" in e for e in errors)

    # 表内声明后通过
    path2 = _write(
        tmp_path,
        """# [Node] A
- **type**: code

```python
transition_to("B", "x")
```

## [Transitions]
- Default -> B

# [Node] B
- **is_final**: true
""",
        name="skill2.md",
    )
    compiled2 = parse_compiled_skill(path2)
    assert validate_node_graph(compiled2.nodes) == []
