#!/usr/bin/env lgskills
# 小型 pi agent 类似物（子图方案验证）
# 多轮交互 + LLM 决策输出工具指令 JSON -> 工具子图执行（带安全边界）-> 回传 -> 汇报

# [Config]
- max_loops: 30

# [Node] Agent
- **type**: llm
- **interactive**: true

你是文件助手，一个支持多轮对话与工具调用的 agent（pi 风格决策循环）。

每轮工作流程（thought → action → 等待观察结果）：
1. **判断需求**：用户本轮需要什么？是否需要文件操作？
   - 需要 → 只输出工具指令 JSON（见下）
   - 不需要 → 直接自然语言回答
2. **工具指令 JSON**（要调用工具时，只输出这个 JSON，不含任何其他文字）：
   - 读取：{"tool": "read_file", "path": "<绝对路径>"}
   - 写入：{"tool": "write_file", "path": "<绝对路径>", "content": "<完整内容>"}
   - 追加：{"tool": "append_file", "path": "<绝对路径>", "content": "<追加内容>"}
   - 列目录：{"tool": "list_dir", "path": "<目录绝对路径>"}
3. **边界**：只允许操作 /tmp/opencode/pi_work 目录内的文件；越界请求一律拒绝，不输出指令。
4. 每轮只做一件事，输出后等待观察结果（工具执行结果会回显给你）。

## [NodeStart]
- **context**: all

## [NodeEnd]
```python
# 检测 LLM 是否输出了工具指令 JSON（以 { 开头）→ 抛 tool_call signal 跳子图
import json as _json
p = str(deliverables.get("payload", "")).strip()
if p.startswith("{"):
    try:
        _json.loads(p)
        signal("tool_call")
    except Exception:
        pass
```

- **on**: loop_count_exceeded(30) :=> give_up

## [Transitions]
| Condition  | Next Node |
| give_up    | GiveUp    |
| tool_call  | ToolExec  |
| Default    | Agent     |

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
    content = tc.get("content", "")
except Exception:
    name = path = content = ""

# 安全边界：只允许在 /tmp/opencode/pi_work 内操作
if not path.startswith("/tmp/opencode/pi_work"):
    result = "Error: 安全边界拒绝 - 路径在允许目录外"
else:
    try:
        if name == "read_file":
            with open(path, "r", encoding="utf-8") as f:
                result = f.read()
        elif name == "write_file":
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            result = f"wrote {len(content)} chars to {path}"
        elif name == "append_file":
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            result = f"appended {len(content)} chars to {path}"
        elif name == "list_dir":
            import os
            items = os.listdir(path)
            result = "\n".join(sorted(items)) if items else "(empty)"
        else:
            result = f"Error: 未知工具 {name}"
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

工具执行结果（observation）：
[tool_result]

向用户简洁汇报这个观察结果：
- 若成功：说明文件内容/写入情况/目录列表
- 若以 Error 开头：说明错误原因，并提示用户正确的用法或路径
结束你的汇报后，等待用户下一轮输入。

## [Transitions]
- Default -> Agent

# [Node] GiveUp
- **is_final**: true
- **type**: code

```python
print("  [GiveUp] 会话轮次上限已到。")
transition_to(None, "give_up")
```
