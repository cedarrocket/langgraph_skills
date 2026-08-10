# scripts/tool_exec.py
# 工具执行节点：解析 payload 中的工具指令 JSON，执行（带安全边界），回传结果。
# 由 pi_agent.md 子图 ToolExec 的 `## [Node] Parse` 引用（- **src**: scripts/tool_exec.py）
#
# 安全边界：只允许操作 ALLOW_ROOT 目录内的文件；越界一律拒绝。

import json
import os

ALLOW_ROOT = "/tmp/opencode/pi_work"

raw = deliverables.get("payload", "")
try:
    tc = json.loads(raw)
    name = tc.get("tool", "")
    path = tc.get("path", "")
    content = tc.get("content", "")
except Exception:
    name = path = content = ""

# 重试计数：出错时最多重试 2 次（RetryFix 修正后回 Parse）
attempts = deliverables.get("tool_attempts", 0)
# 安全边界
if not path.startswith(ALLOW_ROOT):
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
            items = os.listdir(path)
            result = "\n".join(sorted(items)) if items else "(empty)"
        else:
            result = f"Error: 未知工具 {name}"
    except Exception as e:
        result = f"Error: {e}"

deliverables["tool_result"] = result
# 关键：attempts 必须递增，否则 `attempts < 2` 永远成立 → 无限重试
deliverables["tool_attempts"] = attempts + 1

# 自我修正：出错且未超上限 → 回 RetryFix（LLM 看错误修正）；否则子图结束返回主图
if result.startswith("Error:") and attempts < 2:
    messages.append(AIMessage(content=f"[工具执行错误] {result}"))
    transition_to("RetryFix", f"上次指令: {raw}\n执行错误: {result}\n请修正工具指令（只输出新 JSON）。")
else:
    # 工具结果作为消息追加：LangGraph 默认合并回传父图（-> 调用子图）
    messages.append(AIMessage(content=f"[工具结果] {result}"))
    transition_to("Exit", result)
