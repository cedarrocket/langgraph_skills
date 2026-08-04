import pytest

from langgraph_skills.parser import ParseError, parse_compiled_skill


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

# [State] Start
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert any("Unknown section" in w for w in compiled.warnings)
    assert "Start" in compiled.states


def test_unknown_section_strict_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [Config]
- **max_loops**: 5

# [NotASection] hello

# [State] Start
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="Unknown section"):
        parse_compiled_skill(path, strict=True)


def test_duplicate_state_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [State] Start
- **is_final**: true

# [State] Start
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="Duplicate state names"):
        parse_compiled_skill(path)


def test_sequential_fallback(tmp_path):
    # 非 final 且无 transitions 的状态，自动连接声明顺序的下一个
    path = _write(
        tmp_path,
        """# [State] First

do first

# [State] Second

do second

# [State] Final
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.states["First"].transitions[0].next == "Second"
    assert compiled.states["Second"].transitions[0].next == "Final"
    assert compiled.states["Final"].transitions == []


def test_io_reader_writer_generates_reserved_options(tmp_path):
    path = _write(
        tmp_path,
        """# [IO]
- **reader**: txt_reader
- **writer**: txt_writer

# [State] Start
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

# [State] Start
- **max_loops**: 3

# [State] Finish
- **is_final**: true
""",
    )
    compiled = parse_compiled_skill(path)
    assert compiled.max_loops == 15
    assert compiled.states["Start"].max_loops == 3
    assert compiled.states["Finish"].max_loops is None


def test_state_missing_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """# [State]

# [State] Finish
- **is_final**: true
""",
    )
    with pytest.raises(ParseError, match="missing name"):
        parse_compiled_skill(path)
