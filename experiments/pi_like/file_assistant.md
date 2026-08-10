#!/usr/bin/env lgskills
# 文件助手 Agent（交互式工具型 agent 实操样例）
# 验证 DSL 能否实现"超简单 pi agent 类似物"：多轮 turn 循环 + 工具 + 上下文累积 + 生命周期追踪

# [Config]
- max_loops: 30

# [Node] Agent
- **type**: llm
- **interactive**: true
- **tools**: [list_dir, read_text, write_text, append_text]

你是文件助手。用户可以要求你列出目录、读取文件、写入或追加文件内容。
规则：
1. 用户要求读取文件时：直接调用 read_text 读取，不要在中间轮次犹豫或只列目录。
2. 用户要求写入时：直接调用 write_text / append_text。
3. 用户要求列目录时：调用 list_dir。
4. 每次工具调用后，立即向用户汇报工具结果，然后等待下一轮输入。
5. 一次只做一个动作，完成后等待用户下一轮指令。

## [NodeStart]
- **context**: all

## [NodeEnd]
- **on**: loop_count_exceeded(30) :=> give_up

## [Transitions]
| Condition  | Next Node |
| give_up    | GiveUp    |
| Default    | Agent     |

# [Node] GiveUp
- **is_final**: true
- **type**: code

```python
print("  [GiveUp] 会话轮次上限已到，结束。")
transition_to(None, "give_up")
```
