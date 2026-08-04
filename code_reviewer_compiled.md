# [Config]
- **max_loops**: 15
- **reader**: txt_reader

You are an expert Python developer and strict code reviewer. Your goal is to review and fix type annotations for Python code to make it fully compliant with mypy strict mode.

# [State] ReviewCode
- **type**: llm

Analyze the Python code provided in the payload. 
Perform a comprehensive review and output a revised version of the code that:
1. Adds strict Python type hints (annotations) for all function arguments and return types.
2. Resolves standard PEP 8 coding style issues.
3. Fixes any logical bugs or inefficiencies.
4. Avoids using the `Any` type. If necessary, use specific types, Union, Optional, or TypeVar.

You MUST output the complete updated python code wrapped in a single ```python ... ``` code block. Do not include any explanations or discussion outside the code block.

## [Transitions]
- Default -> RunMypy

# [State] RunMypy
- **type**: code

```python
import sys
import subprocess
import tempfile
import os
import re

content = get_payload() or ""

# Extract python code block
match = re.search(r"```python(.*?)```", content, re.DOTALL)
if match:
    code_content = match.group(1).strip()
else:
    code_content = content.strip()

if "def " not in code_content and "class " not in code_content:
    # If LLM failed to output python code block, fallback to current_code
    code_content = deliverables.get("current_code", "")

deliverables["current_code"] = code_content

# Create a temporary file to run mypy check
with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
    tmp.write(code_content)
    tmp_path = tmp.name

try:
    print(f"  [RunMypy] Running mypy check on candidate code...", file=sys.stderr)
    # Get current executable and run mypy --strict
    res = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", tmp_path],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    mypy_success = (res.returncode == 0)
    errors = (res.stdout + "\n" + res.stderr).strip()
    
    attempts = deliverables.get("mypy_attempts", 0) + 1
    deliverables["mypy_attempts"] = attempts
    
    if mypy_success:
        print("  [RunMypy] Mypy validation passed!", file=sys.stderr)
        deliverables["mypy_errors"] = ""
        transition_to("AskApproval", code_content)
    else:
        print(f"  [RunMypy] Mypy validation failed (Attempt {attempts}/3). Errors:\n{errors}", file=sys.stderr)
        deliverables["mypy_errors"] = errors
        if attempts >= 3:
            print("  [RunMypy] Max mypy attempts reached. Transitioning to AskApproval.", file=sys.stderr)
            transition_to("AskApproval", code_content)
        else:
            transition_to("FixMypy", errors)
finally:
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
```

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| Mypy check passed | AskApproval | no | |
| Mypy check failed and attempts < 3 | FixMypy | no | |
| Mypy check failed and attempts >= 3 | AskApproval | no | |

# [State] FixMypy
- **type**: llm

Mypy static type checking failed for your candidate code.
The mypy error output is:
[mypy_errors]

Please modify the code to resolve ALL the mypy errors listed above. 
Ensure that:
1. Every function and method has full type annotations (including self/cls where applicable, arguments, and return types).
2. Avoid using `Any`. Be as specific as possible (e.g., list, dict, Union, Optional).
3. Do not modify the functionality of the original code, only fix the types and style.

Output the complete corrected Python code inside a single ```python ... ``` code block. Do not output anything else.

## [Transitions]
- Default -> RunMypy

# [State] AskApproval
- **type**: llm

The review and mypy type checks of the code are now complete.
Here is the final version of the code that passes (or has been checked by) mypy:

```python
[current_code]
```

Please review the changes above. If you approve of writing these changes back to the original file:
- Respond with 'y' or 'yes' to write back the file.
- If you have feedback, write it down to ask for further modifications.

## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| User approves | WriteBack | yes | Approving and writing to file. |
| User rejects or requests change | ReviewCode | no | User requested changes. |

# [State] WriteBack
- **type**: code

```python
import sys

filepath = deliverables.get("input_path") or deliverables.get("input")
code = deliverables.get("current_code")

if filepath and code:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"  [WriteBack] Successfully wrote updated code to '{filepath}'", file=sys.stderr)
    transition_to("Finish", f"Successfully reviewed and updated {filepath}")
else:
    print("  [WriteBack] Error: Filepath or code is missing.", file=sys.stderr)
    transition_to("Finish", "Error: Missing filepath or code in deliverables.")
```

## [Transitions]
- Default -> Finish

# [State] Finish
- **is_final**: true

The review task is complete! The final code has been saved back to the file.