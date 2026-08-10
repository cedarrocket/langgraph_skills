"""工具边界 hooks（pre_tool / post_tool / post_tool_failure）。

对标 Claude Code 的 PreToolUse / PostToolUse / PostToolUseFailure：
统一的"工具调用边界介入"层，用独立 hooks.json 配置（全局 + 项目，列表拼接），
handler 复用 type:script 注入环境（deliverables/messages/get_payload/compact）。

hooks.json 格式：
{
  "hooks": {
    "pre_tool":   [{ "matcher": "*", "handler": "pre_tool.py" }],
    "post_tool":  [{ "matcher": "read_file", "handler": "audit.py" }],
    "post_tool_failure": [{ "matcher": "*", "handler": "fallback.py" }]
  }
}
matcher：工具名（支持 * 通配，如 "read_*"、"mcp__github__*"）。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

HOOKS_CONFIG_NAME = "hooks.json"

CHECKPOINT_PRE_TOOL = "pre_tool"
CHECKPOINT_POST_TOOL = "post_tool"
CHECKPOINT_POST_TOOL_FAILURE = "post_tool_failure"

TOOL_HOOK_CHECKPOINTS = {
    CHECKPOINT_PRE_TOOL,
    CHECKPOINT_POST_TOOL,
    CHECKPOINT_POST_TOOL_FAILURE,
}


@dataclass
class ToolHookRule:
    """一条工具边界 hook 规则：matcher 匹配工具名，命中则执行 handler。"""

    checkpoint: str = CHECKPOINT_PRE_TOOL
    matcher: str = "*"
    handler: str = ""


@dataclass
class ToolHooks:
    """工具边界 hooks 集合（按检查点分组）。"""

    rules: List[ToolHookRule] = field(default_factory=list)

    def for_checkpoint(self, checkpoint: str) -> List[ToolHookRule]:
        return [r for r in self.rules if r.checkpoint == checkpoint]

    def matches(self, rule: ToolHookRule, tool_name: str) -> bool:
        if not rule.matcher or rule.matcher == "*":
            return True
        return fnmatch.fnmatch(tool_name, rule.matcher)


def load_hooks(
    global_path: Optional[Path] = None,
    project_path: Optional[Path] = None,
) -> ToolHooks:
    """加载 hooks.json（全局 + 项目，规则拼接非覆盖）。"""
    from langgraph_skills.config import _global_config_path

    rules: List[ToolHookRule] = []
    paths: List[Path] = []
    gp = global_path or _global_config_path().with_name(HOOKS_CONFIG_NAME)
    pp = project_path or _hooks_project_path()
    if gp is not None:
        paths.append(gp)
    if pp is not None:
        paths.append(pp)

    for path in paths:
        if path is None or not path.exists():
            continue
        try:
            data = _load_json(path)
        except Exception as e:
            print(f"  [Hooks] Failed to load {path}: {e}")
            continue
        hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
        if not isinstance(hooks, dict):
            continue
        for checkpoint, items in hooks.items():
            if checkpoint not in TOOL_HOOK_CHECKPOINTS:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                rules.append(
                    ToolHookRule(
                        checkpoint=checkpoint,
                        matcher=str(item.get("matcher", "*")),
                        handler=str(item.get("handler", "")),
                    )
                )
    return ToolHooks(rules=rules)


def _hooks_project_path() -> Optional[Path]:
    """项目目录下查找 hooks.json（与 lgskills.json 同级）。"""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / HOOKS_CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def _load_json(path: Path) -> Dict[str, Any]:
    import json

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fire_tool_hooks(
    hooks: ToolHooks,
    checkpoint: str,
    scope: Dict[str, Any],
    tool_name: str,
) -> None:
    """执行指定检查点、匹配 tool_name 的所有 hook handler。"""
    from langgraph_skills.triggers import run_handler

    for rule in hooks.for_checkpoint(checkpoint):
        if hooks.matches(rule, tool_name) and rule.handler:
            run_handler(rule.handler, scope)
