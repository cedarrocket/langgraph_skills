import os
import re
import subprocess

# 1. 从对话历史 messages 中提取最新的 AIMessage（包含 Python 代码）
code_content = None
for msg in reversed(messages):
    # msg.__class__.__name__ is used to bypass type import issues
    if msg.__class__.__name__ == 'AIMessage' and "def " in msg.content:
        # Extract code from Markdown code block if present
        match = re.search(r"```python(.*?)```", msg.content, re.DOTALL)
        if match:
            code_content = match.group(1).strip()
        else:
            code_content = msg.content.strip()
        break

if not code_content:
    print("  [Script Executing] Error: No python code found in AI message history.")
    transition_to("WriteCode", "No Python code blocks were found. Please generate the full Python code.")
else:
    # 2. 将代码写入临时文件
    tmp_filename = "temp_agent_output.py"
    with open(tmp_filename, "w", encoding="utf-8") as f:
        f.write(code_content)
    
    print(f"  [Script Executing] Running mypy check on: {tmp_filename}")
    
    import sys
    # 3. 运行 mypy 检查
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", tmp_filename],
        capture_output=True, text=True, encoding="utf-8"
    )
    
    success = result.returncode == 0
    errors = result.stdout if result.stdout else result.stderr
    
    # 4. 统计修复次数，防止无限循环
    attempts = deliverables.get("mypy_attempts", 0) + 1
    deliverables["mypy_attempts"] = attempts
    
    if success:
        print("  [Script Executing] Mypy check passed successfully!")
        transition_to("Publish", code_content)
    else:
        print(f"  [Script Executing] Mypy check failed (Attempt {attempts}/3).")
        if attempts >= 3:
            print("  [Script Executing] Max attempts reached. Routing to Publish despite mypy errors.")
            transition_to("Publish", f"Mypy errors remaining:\n{errors}\n\nCode:\n{code_content}")
        else:
            print("  [Script Executing] Routing to FixCode with errors.")
            transition_to("FixCode", f"Code to be fixed:\n```python\n{code_content}\n```\n\nMypy check failed with the following errors:\n{errors}\n\nPlease fix these errors in the code and output the full updated code.")
