import os
import tempfile

from langgraph.graph.state import CompiledStateGraph

from langgraph_skills.graph import build_graph
from langgraph_skills.models import StateInfo, Transition
from langgraph_skills.parser import parse_compiled_skill, parse_state_body, validate_state_graph


def test_parse_state_body():
    body_text = """- **type**: llm
- **tools**: web_search, read_file
- **history_window**: 5
- **interactive**: true
- **is_final**: false

Please read the file and search the query.

## [Output JSON]
```json
{
  "type": "object",
  "properties": {
    "result": { "type": "string" }
  },
  "required": ["result"]
}
```

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| Success | Finish | yes | Done! |
| Fail | Retry | no | Fix it |
"""
    result = parse_state_body("TestState", body_text)

    assert result.name == "TestState"
    assert result.state_type == "llm"
    assert result.tools == ["web_search", "read_file"]
    assert result.history_window == 5
    assert result.interactive is True
    assert result.is_final is False
    assert "Please read the file" in result.instructions
    assert isinstance(result.output_schema, dict)
    assert result.output_schema["required"] == ["result"]
    assert len(result.transitions) == 2

    # Check transitions
    t1 = result.transitions[0]
    assert t1.condition == "Success"
    assert t1.next == "Finish"
    assert t1.require_approval is True
    assert t1.feedback == "Done!"


def test_validate_state_graph():
    # Valid graph
    valid_states = {
        "Start": StateInfo("Start", "task", [Transition(next="Finish")], is_final=False),
        "Finish": StateInfo("Finish", "task", [], is_final=True),
    }
    assert len(validate_state_graph(valid_states)) == 0

    # Dangling transition
    invalid_states = {
        "Start": StateInfo("Start", "task", [Transition(next="NonExistent")], is_final=False)
    }
    errors = validate_state_graph(invalid_states)
    assert len(errors) == 1
    assert "targeting non-existent state" in errors[0]

    # Non-final state without transitions at the end
    invalid_states2 = {
        "Start": StateInfo("Start", "task", [], is_final=False)
    }
    errors2 = validate_state_graph(invalid_states2)
    assert len(errors2) == 1
    assert "missing a '## [Transitions]' definition" in errors2[0]


def test_parse_compiled_skill():
    skill_content = """This is a global prompt.

# [Config]
- **max_loops**: 15
- **reader**: txt_reader

# [State] Start
- **type**: code

```python
transition_to("Finish", "ok")
```

## [Transitions]
- Default -> Finish

# [State] Finish
- **is_final**: true
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(skill_content)
        tmp_path = tmp.name

    try:
        compiled = parse_compiled_skill(tmp_path)

        assert compiled.global_text == "This is a global prompt."
        assert compiled.max_loops == 15
        assert "Start" in compiled.states
        assert "Finish" in compiled.states
        assert compiled.states["Start"].state_type == "code"
        assert compiled.states["Finish"].is_final is True

        # Verify reader option was added (from Config, backward compat)
        has_reader_option = any(o.reader == "txt_reader" for o in compiled.input_options)
        assert has_reader_option is True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_build_graph():
    skill_content = """This is global prompt.

# [Config]
- **max_loops**: 5

# [State] Start
- **type**: code

```python
transition_to("Finish", "ok")
```

## [Transitions]
- Default -> Finish

# [State] Finish
- **is_final**: true
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(skill_content)
        tmp_path = tmp.name

    try:
        app = build_graph(tmp_path)
        assert isinstance(app, CompiledStateGraph)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
