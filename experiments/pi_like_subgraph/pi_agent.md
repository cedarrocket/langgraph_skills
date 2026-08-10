#!/usr/bin/env lgskills
# 小型 pi agent 类似物（子图方案验证）
# 多轮交互 + LLM 决策输出工具指令 JSON -> 工具子图执行（带安全边界）-> 回传 -> 汇报

# [Config]
- max_loops: 30

# [Node] Agent
- **type**: llm
- **interactive**: true

你是文件助手，可帮用户读取/写入/追加文件（仅限 /tmp/opencode/pi_work 目录内）。
工作方式：
1. 若用户要求文件操作，你必须输出一个 JSON 对象（不要任何其他文字）：
   {"tool": "<read_file|write_file|append_file>", "path": "<绝对路径>", "content": "<内容，仅写/追加时需要>"}
2. 若用户没有要求文件操作，直接以自然语言回答。
3. 每轮只能做一件事：要么输出 JSON 指令，要么直接回答。然后等待用户下一轮输入。

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

工具执行结果：
[tool_result]

用一两句话向用户简洁汇报这个结果。若结果以 Error 开头，说明错误原因。

## [Transitions]
- Default -> Agent

# [Node] GiveUp
- **is_final**: true
- **type**: code

```python
print("  [GiveUp] 会话轮次上限已到。")
transition_to(None, "give_up")
```
