#!/usr/bin/env lgskills
# 工具执行子图（被 pi_agent.md 通过 `# [SubGraph] ToolExec - **src**: tool_subagent.md` 引用）
# 子图内所有路径汇聚到 Exit（is_final），Exit 返回主图（next_state 指向主图 Agent）。

# [Node] Parse
- **type**: script
- **src**: scripts/tool_exec.py

## [Transitions]
- Default -> Exit
- On error -> RetryFix

# [Node] RetryFix
- **type**: llm

上一次工具调用失败：
[tool_result]

请根据错误修正工具调用指令。只输出一个新的工具指令 JSON（read_file / write_file / append_file / list_dir），不要任何其他文字。
注意安全边界：只能操作 /tmp/opencode/pi_work 目录内的文件。

## [Transitions]
- Default -> Parse

# [Node] Exit
- **type**: code
- **is_final**: true

```python
# 子图出口：next_state 指向主图 Agent，由父图 _sub_after_ 路由回 Agent，
# 让 Agent 基于工具结果自动汇报（AI: 打印）后，Default -> Input 等用户输入。
# 工具结果消息已由 tool_exec.py 追加进 messages（LangGraph 默认合并回父图）
transition_to("Agent", deliverables.get("tool_result", ""))
```
