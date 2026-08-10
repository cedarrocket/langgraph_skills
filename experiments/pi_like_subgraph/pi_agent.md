#!/usr/bin/env lgskills
# 小型 pi agent 类似物（子图方案 + 代码节点交互）
# 多轮交互由 Input(code 节点) 驱动；Agent 只做决策；工具调用走独立子图（tool_subagent.md）
# 所有硬编码 Python 外嵌到 scripts/（input.py / agent_end.py / tool_exec.py）

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
- **src**: scripts/agent_end.py

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
- On quit -> GiveUp

# [SubGraph] ToolExec
- **src**: tool_subagent.md

# [Node] GiveUp
- **is_final**: true
- **type**: code

```python
print("  [GiveUp] 会话结束。")
transition_to(None, "give_up")
```
