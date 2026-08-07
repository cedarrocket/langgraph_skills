"""Trigger（触发器）机制：统一的"条件 → 处理程序"介入层。

对应 PROCESS.md §7.6 计划书：
  - 统一的触发器概念，取代复杂的 middleware 栈
  - condition：Python 条件表达式 或 pyfunction:xxx.py（返回 True 即触发）
  - on_trigger：外部处理程序（复用 type:script 注入环境）
  - 检查点隐式映射：context_length→pre_llm、loop_count→post_node、error_flag→on_error
  - 表达式解析时静态检查（语法错误 / 未定义变量），非运行时

安全架子（MVP 留空）：ALLOWED_AST_NODES + _check_ast_allowlist()，
未来可收紧条件表达式的允许节点类型。
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# 检查点类型
CHECKPOINT_PRE_LLM = "pre_llm"
CHECKPOINT_POST_NODE = "post_node"
CHECKPOINT_ON_ERROR = "on_error"

# 条件表达式可访问的作用域变量（MVP 全开放，检查是否存在）
SCOPE_VARS = {
    "context_length",
    "loop_count",
    "error_flag",
    "deliverables",
    "messages",
    "current_node",
    "max_loops",
    "next_state",
}

# 安全架子：允许的 AST 节点类型白名单（MVP 不启用，None 表示不限制）
ALLOWED_AST_NODES: Optional[set] = None


class TriggerError(Exception):
    """触发器配置/求值错误。"""


@dataclass
class Trigger:
    """一个触发器：条件满足时调用处理程序。"""

    condition: str = ""  # Python 表达式 或 "pyfunction:path.py"
    on_trigger: str = ""  # 处理程序脚本路径
    checkpoint: str = CHECKPOINT_PRE_LLM  # 隐式检查点（用户可覆盖）
    scope: str = "global"  # global | node:<name>
    enabled: bool = True

    @property
    def is_pyfunction(self) -> bool:
        return self.condition.startswith("pyfunction:")


# ---------------------------------------------------------------------------
# 表达式静态检查（解析时，非运行时）
# ---------------------------------------------------------------------------


def check_condition_expr(expr: str, scope_vars: set[str] | None = None) -> None:
    """静态检查条件表达式：语法错误 / 未定义变量。失败抛 TriggerError。"""
    if not expr or expr.startswith("pyfunction:"):
        return
    scope = scope_vars or SCOPE_VARS
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise TriggerError(f"Condition expression syntax error: {expr!r}: {e}") from e

    # 收集 Name 节点，比对作用域变量
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in scope:
                raise TriggerError(
                    f"Condition expression references undefined variable '{node.id}' in {expr!r}. "
                    f"Available: {sorted(scope)}"
                )

    # 安全架子（未来启用）：检查 AST 节点类型白名单
    if ALLOWED_AST_NODES is not None:
        for node in ast.walk(tree):
            if type(node) not in ALLOWED_AST_NODES:
                raise TriggerError(
                    f"Condition expression uses disallowed construct {type(node).__name__} in {expr!r}"
                )


# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------


def evaluate_condition(trigger: Trigger, scope: Dict[str, Any]) -> bool:
    """求值一个触发器条件。pyfunction 返回 True 即触发。"""
    if not trigger.enabled:
        return False
    if trigger.is_pyfunction:
        return _eval_pyfunction(trigger.condition[len("pyfunction:") :], scope)
    # Python 表达式
    check_condition_expr(trigger.condition, set(scope.keys()) | SCOPE_VARS)
    try:
        result = eval(trigger.condition, {"__builtins__": {}}, scope)
    except Exception as e:
        print(f"  [Trigger] Condition evaluation failed: {e}")
        return False
    return bool(result)


def _eval_pyfunction(script_path: str, scope: Dict[str, Any]) -> bool:
    """执行 pyfunction 脚本，返回 True 即触发。

    注入环境：deliverables/messages/get_payload + 只读的当前状态。
    脚本可用 `trigger_result(True)` 显式触发，或直接 `return True`（exec 不捕获 return，
    故统一用 trigger_result 语义；也支持脚本内定义 result 变量）。
    """
    if not os.path.exists(script_path):
        print(f"  [Trigger] pyfunction file not found: {script_path}")
        return False

    def transition_to(*args: Any, **kwargs: Any) -> None:
        raise TriggerError("pyfunction conditions must not call transition_to.")

    local_vars: Dict[str, Any] = {
        "deliverables": scope.get("deliverables", {}),
        "messages": scope.get("messages", []),
        "get_payload": lambda: scope.get("deliverables", {}).get("payload"),
        "context_length": scope.get("context_length", 0),
        "loop_count": scope.get("loop_count", 0),
        "error_flag": scope.get("error_flag", False),
        "current_node": scope.get("current_node", ""),
        "transition_to": transition_to,
        "_trigger_fired": False,
    }

    def trigger_result(value: bool) -> None:
        local_vars["_trigger_fired"] = bool(value)

    local_vars["trigger_result"] = trigger_result
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()
        exec(code, {}, local_vars)
    except Exception as e:
        print(f"  [Trigger] pyfunction execution failed: {e}")
        return False
    return bool(local_vars.get("_trigger_fired", False))


# ---------------------------------------------------------------------------
# 检查点分发
# ---------------------------------------------------------------------------
# 处理程序执行：复用 type:script 注入模式。处理程序自行决定改状态/拦截/重试。


def run_handler(handler_path: str, scope: Dict[str, Any]) -> None:
    """执行 on_trigger 处理程序（复用 script 注入环境）。"""
    if not os.path.exists(handler_path):
        print(f"  [Trigger] Handler file not found: {handler_path}")
        return
    local_vars = {
        "deliverables": scope.get("deliverables", {}),
        "messages": scope.get("messages", []),
        "get_payload": lambda: scope.get("deliverables", {}).get("payload"),
        "transition_to": scope.get("transition_to", lambda *a, **k: None),
        "context_length": scope.get("context_length", 0),
        "loop_count": scope.get("loop_count", 0),
        "error_flag": scope.get("error_flag", False),
        "current_node": scope.get("current_node", ""),
    }
    try:
        with open(handler_path, "r", encoding="utf-8") as f:
            code = f.read()
        exec(code, {}, local_vars)
    except Exception as e:
        print(f"  [Trigger] Handler execution failed: {e}")


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

TriggerList = List[Trigger]


def load_triggers_from_config(config: Dict[str, Any], checkpoint: Optional[str] = None) -> TriggerList:
    """从配置 dict 的 'triggers' 段解析触发器列表。"""
    raw = config.get("triggers", [])
    triggers: TriggerList = []
    if not isinstance(raw, list):
        return triggers
    for item in raw:
        if not isinstance(item, dict):
            continue
        condition = item.get("condition", "")
        if not condition:
            continue
        t = Trigger(
            condition=condition,
            on_trigger=item.get("on_trigger", ""),
            checkpoint=item.get("checkpoint", checkpoint or CHECKPOINT_PRE_LLM),
            enabled=item.get("enabled", True) is not False,
        )
        check_condition_expr(t.condition)  # 静态检查（解析时）
        triggers.append(t)
    return triggers


def triggers_for_checkpoint(triggers: TriggerList, checkpoint: str) -> TriggerList:
    """筛选指定检查点的触发器。"""
    return [t for t in triggers if t.checkpoint == checkpoint]
