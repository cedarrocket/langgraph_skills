# validate_syntax.py
# 用途：自举编译器 Skill（bootstrap_compiled.md）的 type=script 节点挂载脚本。
# 该脚本对 LLM 编译产出的 Markdown 调用 `lgskills validate` 做语法校验，
# 并根据结果跳转 FixCompilation（失败）或 Finish（通过）。
# 通过 # [Node] 的 src 属性被引用：`- **src**: validate_syntax.py`

import os
import subprocess
import sys
import tempfile

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
        encoding="utf-8",
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
