#!/usr/bin/env lgskills

# [Config]
- **reader**: txt_reader
- **writer**: txt_writer

You are a LangGraph Skill Compiler.

# [Node] CompileDraft
You are a LangGraph Skill Compiler.
Your task is to read the draft skill provided in the payload and compile it into a standard Markdown AST state machine format.

Rules:
1. Every State must be declared as a top-level heading: `# [Node] StateName`.
   - If it is the final state, it must have `- **is_final**: true` or `- is_final: true` listed as a bullet point at the top of the state block.
   - All other metadata attributes of the state (such as `type`, `src`, `interactive`, and `tools`) MUST be preserved as list properties directly under the `# [Node] StateName` heading, like this:
     - **type**: llm
     - **tools**: web_search
2. Transition logic must be compiled into a sub-section: `## [Transitions]`.
   - For multiple conditional transitions (e.g., table or rules), use a Markdown table:
     | Condition | Next Node | Require Approval | Feedback |
     | :--- | :--- | :--- | :--- |
     | expression_1 | State_A | yes | msg_A |
     | expression_2 | State_B | no | msg_B |
   - For a single unconditional transition, you can just use a list item:
     - Default -> TargetState
   - If the user wrote informal transition descriptions or shorthand (e.g., "go to Win", "跳转到 Finish"), translate them into standard list or table transitions.
   - If the user did not write any transition logic for a non-final state, do not output any transitions; the interpreter will automatically fallback to sequential execution.
3. Keep the original global instructions (text outside states) and state task instructions (text inside states) unchanged, except for formatting them into standard markdown blocks.
4. Output ONLY the compiled Markdown containing the valid `# [Node]` and `## [Transitions]` structures. Do not wrap the output in markdown code blocks unless the input draft itself was wrapped.
5. If the input draft has a shebang line (e.g., #!...) or a # [Config] block, you MUST fully preserve them exactly as-is at the very top of the compiled output.

Please compile the draft content provided below:

## [Transitions]
- Default -> ValidateSyntax

# [Node] ValidateSyntax
- **type**: code

```python
import sys
import subprocess
import tempfile
import os

compiled_content = deliverables.get("payload", "")

# Clean code block wrappers if any
clean_content = compiled_content.strip()
if clean_content.startswith("```"):
    lines = clean_content.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    clean_content = "\n".join(lines).strip()

# Create a temporary file to run the validation check
with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
    tmp.write(clean_content)
    tmp_path = tmp.name

try:
    res = subprocess.run(
        [sys.executable, "-m", "langgraph_skills.cli", "validate", tmp_path],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if res.returncode != 0:
        deliverables["validation_error"] = res.stderr.strip()
        print(f"  [Validation Failed] Routing to FixCompilation. Error:\n{res.stderr.strip()}", file=sys.stderr)
        transition_to("FixCompilation", clean_content)
    else:
        print("  [Validation] Compiled Markdown is valid.", file=sys.stderr)
        if "validation_error" in deliverables:
            del deliverables["validation_error"]
        deliverables["payload"] = clean_content
        transition_to("Finish", clean_content)
finally:
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
```

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| validation fails | FixCompilation | no | |
| validation passes | Finish | yes | |

# [Node] FixCompilation
Your previous compilation attempt failed validation.
Please correct the compiled state machine code according to the validation error.

Instructions:
1. Analyze the original draft (in `input_path_content`) and your failed compilation attempt (in Context).
2. Examine the validation error (in `validation_error`).
3. Correct the state declarations, transitions, or is_final flags to ensure it matches the new syntax guidelines.
4. Output ONLY the corrected compiled Markdown state machine. Do not explain, do not wrap in code blocks.

## [Transitions]
- Default -> ValidateSyntax

# [Node] Finish
- **type**: code
- **is_final**: true

```python
transition_to(None, get_payload())
```