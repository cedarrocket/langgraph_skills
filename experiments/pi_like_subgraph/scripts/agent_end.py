# scripts/agent_end.py
# Agent 节点的 NodeEnd executor（外嵌）：决策工具调用或打印文本回答。
# 由 pi_agent.md 的 `## [NodeEnd] - **src**: scripts/agent_end.py` 引用。
#
# 逻辑：
# - payload 含工具指令 JSON 且最近不是工具结果（非汇报轮）→ signal("tool_call") 跳工具子图
# - 否则（文本回答 / 汇报轮）→ 打印 AI: 显示给用户

import json as _json
import re as _re

p = str(deliverables.get("payload", "")).strip()

# 若最近一条消息是工具结果（子图刚返回），本轮是"汇报轮"：
# 即使 payload 是工具指令 JSON（重复请求），也不触发 tool_call，避免死循环。
# 汇报完成后回 Input 等用户新输入，新输入到达后才恢复决策。
last_msg = messages[-1] if messages else None
just_returned_from_tool = (
    last_msg is not None
    and getattr(last_msg, "content", "") is not None
    and "[工具结果]" in str(getattr(last_msg, "content", ""))
)

m = _re.search(r"\{.*\}", p, _re.DOTALL)
if m and not just_returned_from_tool:
    try:
        _json.loads(m.group(0))
        deliverables["payload"] = m.group(0)
        signal("tool_call")
    except Exception:
        # 有 {} 但非合法 JSON：当作文本回答
        print(f"\nAI: {p}\n")
else:
    # 纯文本回答或汇报轮：显示给用户
    if p:
        print(f"\nAI: {p}\n")
