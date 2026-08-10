# scripts/input.py
# 交互输入节点：读取用户一行输入，追加为 HumanMessage，跳转回 Agent。
# 由 pi_agent.md 的 `## [Node] Input` 引用（- **src**: scripts/input.py）

line = safe_input("You: ")
if line.strip().lower() in ("退出", "quit", "exit", "q"):
    transition_to("GiveUp", "bye")
else:
    # 追加为用户消息（进入 messages，供 LLM 下一轮读取）
    messages.append(HumanMessage(content=line.strip()))
    transition_to("Agent", line.strip())
