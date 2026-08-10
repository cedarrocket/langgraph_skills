# [Config]
- max_loops: 10

# [Node] Agent
- **type**: llm

你是文件助手。用户要求你读取文件时，你必须输出一个 JSON 对象（不要其他文字）：
{"tool": "read_file", "path": "<文件路径>"}
只有当你确定用户没有要求任何文件操作时，才输出普通文本回答。

用户请求：读取 /tmp/opencode/subtest.txt 的内容。

## [Transitions]
- Default ==> ToolExec <==

# [SubGraph] ToolExec
## [Node] Parse
- **type**: code

```python
import json

raw = deliverables.get("payload", "")
try:
    tc = json.loads(raw)
    name = tc.get("tool", "")
    path = tc.get("path", "")
except Exception:
    tc = None
    name = ""
    path = ""

# 安全边界：只允许读 /tmp/opencode 内
if not path.startswith("/tmp/opencode"):
    result = "Error: 安全边界拒绝 - 路径在允许目录外"
else:
    try:
        with open(path, "r", encoding="utf-8") as f:
            result = f.read()
    except Exception as e:
        result = f"Error: {e}"

deliverables["tool_result"] = result
deliverables["_child_messages"] = messages[-1:]
transition_to(None, result)
```

## [Transitions]
- Default -> Report

# [Node] Report
- **type**: llm

读取结果如下：
[tool_result]

向用户汇报这个文件的完整内容。

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
- **type**: code

```python
transition_to(None, deliverables.get("tool_result", ""))
```
