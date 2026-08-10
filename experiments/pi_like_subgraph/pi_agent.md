#!/usr/bin/env lgskills
# 小型 pi agent 类似物（子图方案 + 代码节点交互）
# 多轮交互由 Input(code 节点) 驱动；Agent 只做决策；工具调用走子图（Python 外嵌 + 安全边界）

# [Config]
- max_loops: 40

# [Node] Agent
- **type**: llm

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
4. 每轮只做一件事：要么输出工具指令 JSON，要么自然语言回答。然后等 Input 节点读取下一轮用户输入。

## [NodeStart]
- **context**: all

## [NodeEnd]
```python
import json as _json, re as _re
p = str(deliverables.get("payload", "")).strip()
m = _re.search(r"\{.*\}", p, _re.DOTALL)
if m:
    try:
        _json.loads(m.group(0))
        deliverables["payload"] = m.group(0)
        signal("tool_call")
    except Exception:
        # 有 {} 但非合法 JSON：当作文本回答
        print(f"\nAI: {p}\n")
else:
    # 纯文本回答：显示给用户
    if p:
        print(f"\nAI: {p}\n")
```

- **on**: loop_count_exceeded(40) :=> give_up

## [Transitions]
| Condition  | Next Node |
| give_up    | GiveUp    |
| tool_call  | ToolExec  |
| Default    | Input     |
# [Node] Input
- **type**: script
- **src**: scripts/input.py

## [Transitions]
- Default -> Agent

# [SubGraph] ToolExec
## [Node] Parse
- **type**: script
- **src**: scripts/tool_exec.py

## [Transitions]
- Default -> Done

## [Node] RetryFix
- **type**: llm

上一次工具调用失败：
[tool_result]

请根据错误修正工具调用指令。只输出一个新的工具指令 JSON（read_file / write_file / append_file / list_dir），不要任何其他文字。
注意安全边界：只能操作 /tmp/opencode/pi_work 目录内的文件。

## [Transitions]
- Default -> Parse

## [Node] Done
- **type**: code
- **is_final**: true

```python
# 子图结束：next_state 指向主图 Agent，由父图 _sub_after_ 路由回 Agent，
# 让 Agent 基于工具结果自动汇报（AI: 打印）后，Default -> Input 等用户输入。
# 工具结果消息已由 tool_exec.py 追加进 messages（LangGraph 默认合并回父图）
transition_to("Agent", deliverables.get("tool_result", ""))
```

# [Node] GiveUp
- **is_final**: true
- **type**: code

```python
print("  [GiveUp] 会话结束。")
transition_to(None, "give_up")
```
