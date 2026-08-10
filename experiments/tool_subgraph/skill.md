# [Config]
- max_loops: 10

# [Node] Orchestrator
- **type**: code

```python
# 模拟 LLM 决定调用工具：写入结构化指令到 deliverables
deliverables["tool_call"] = {"name": "read_file", "args": {"path": "/tmp/opencode/subtest.txt"}}
transition_to("Tool", "invoke")
```

## [Transitions]
- Default ==> Tool <==

# [SubGraph] Tool
## [Node] Exec
- **type**: code

```python
# 子图内执行工具（带安全边界：只允许读实验目录）
import os, json

tc = deliverables.get("tool_call", {})
name = tc.get("name")
args = tc.get("args", {})
path = args.get("path", "")

# 安全边界：限制在 /tmp/opencode 内
allow_root = "/tmp/opencode"
if not path.startswith(allow_root):
    result = "Error: 安全边界拒绝 - 路径在允许目录外"
else:
    try:
        with open(path, "r", encoding="utf-8") as f:
            result = f.read()
    except Exception as e:
        result = f"Error: {e}"

deliverables["tool_result"] = result
deliverables["_child_messages"] = messages[-1:]  # 回传（保持消息不增长）
transition_to(None, result[:50])
```

## [Transitions]
- Default -> Done

# [Node] Done
- **type**: code
- **is_final**: true

```python
print(f"  [Done] tool_result = {deliverables.get('tool_result', '')[:60]}")
transition_to(None, deliverables.get("tool_result", ""))
```
