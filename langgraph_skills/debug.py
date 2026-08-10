"""调试输出开关（quiet 静默模式）。

默认 debug（全信息输出）；`lgskills run --quiet` 时全局静默调试日志，
仅保留交互提示（AI:/You:）与错误/警告。

调试日志统一走 `dprint()`；用户可见/错误输出保持 `print()`。
"""

from __future__ import annotations

DEBUG_PRINT_ENABLED = True


def set_debug_print(enabled: bool) -> None:
    """全局设置调试打印开关（进程级运行策略）。"""
    global DEBUG_PRINT_ENABLED
    DEBUG_PRINT_ENABLED = bool(enabled)


def dprint(*args, **kwargs) -> None:
    """调试日志：quiet 模式下静默，调试模式下原样输出。"""
    if DEBUG_PRINT_ENABLED:
        print(*args, **kwargs)
