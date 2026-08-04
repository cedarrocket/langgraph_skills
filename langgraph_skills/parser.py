"""DSL 前端解析器：markdown 文本 -> CompiledSkill (IR)。

对应 PROCESS.md 设计基线的"语法分析"层：
  行分类/分块 -> 区块解析 -> IR（models.CompiledSkill）

职责：
  - 解析文档结构（shebang / 全局文本 / 顶层 section / 子 section）
  - 各 section 语义（Config / IO / Tools / State）
  - 结构性归一化：IO 保留参数生成、节点级 max_loops、隐式顺序 fallback
  - 容错：未知 section -> warning；重复 state 名 -> error

不负责：执行（executors）、图构建（graph）、语义深度校验（validator）。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from langgraph_skills.models import (
    RESERVED_INPUT,
    RESERVED_OUTPUT,
    CompiledSkill,
    InputOption,
    StateInfo,
    ToolInfo,
    Transition,
)

# 顶层 section 类型（key 转小写后匹配）
_SECTION_CONFIG = "config"
_SECTION_IO = "io"
_SECTION_TOOLS = "tools"
_SECTION_STATE = "state"

_KNOWN_SECTIONS = {_SECTION_CONFIG, _SECTION_IO, _SECTION_TOOLS, _SECTION_STATE}


class ParseError(Exception):
    """结构化解析错误（例如重复状态名）。"""


# ---------------------------------------------------------------------------
# 1. 嵌套列表解析（`- **key**: value` / 缩进子项）
# ---------------------------------------------------------------------------


def parse_nested_markdown_list(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    lines = text.splitlines()
    current_key: Optional[str] = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())

        if stripped.startswith(("-", "*", "+")):
            item_content = stripped[1:].strip()
            if ":" in item_content:
                k, v_str = item_content.split(":", 1)
                k = k.strip().replace("**", "")
                v_str = v_str.strip().strip('"').strip("'")

                v_val: Any = v_str
                if indent == 0:
                    current_key = k
                    data[k] = v_val if v_val else {}
                else:
                    if current_key:
                        target_dict = data.get(current_key)
                        if not isinstance(target_dict, dict):
                            target_dict = {}
                            data[current_key] = target_dict
                        if v_str.lower() == "true":
                            v_val = True
                        elif v_str.lower() == "false":
                            v_val = False
                        target_dict[k] = v_val
    return data


# ---------------------------------------------------------------------------
# 2. 状态转移解析（表格 / 列表）
# ---------------------------------------------------------------------------


def parse_markdown_table_transitions(lines: list) -> List[Transition]:
    transitions: List[Transition] = []
    header = None
    for line in lines:
        stripped = line.strip()
        if not stripped or re.match(r"^\|\s*[:\-]", stripped) or stripped.startswith(("-:", "|:", "|-")):
            continue
        parts = [p.strip() for p in line.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        if not parts:
            continue

        if header is None:
            header = [p.lower() for p in parts]
            continue

        row_dict = dict(zip(header, parts))
        next_state = row_dict.get("next state") or row_dict.get("next") or row_dict.get("next_state")
        if next_state:
            cond = row_dict.get("condition") or row_dict.get("cond")
            if cond and cond.lower() in ("default", "none", "", "any"):
                cond = None
            feedback = row_dict.get("feedback")
            if feedback == "":
                feedback = None
            req_app_val = row_dict.get("require approval") or row_dict.get("require_approval") or ""
            require_approval = req_app_val.lower() in ("yes", "true")
            transitions.append(
                Transition(
                    condition=cond,
                    next=next_state,
                    feedback=feedback,
                    require_approval=require_approval,
                )
            )
    return transitions


def parse_markdown_list_transitions(lines: list) -> List[Transition]:
    transitions: List[Transition] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "+")):
            item = stripped[1:].strip()

            # Default -> TargetState
            if "default" in item.lower() and "->" in item:
                right_side = item.split("->", 1)[1].strip()
                require_approval = "[require approval]" in right_side.lower() or "(require approval)" in right_side.lower()
                next_state = right_side.split("(")[0].split("[")[0].strip()
                transitions.append(
                    Transition(next=next_state, require_approval=require_approval)
                )
                continue

            # If `cond` -> TargetState (Feedback: "...") [Require Approval]
            if "if" in item.lower() and "->" in item:
                cond_match = re.search(r"(?i)if\s+`([^`]+)`", item)
                if cond_match:
                    cond = cond_match.group(1).strip()
                else:
                    cond = item.split("->", 1)[0].replace("if", "", 1).strip()

                right_side = item.split("->", 1)[1].strip()
                require_approval = "[require approval]" in right_side.lower() or "(require approval)" in right_side.lower()

                feedback = None
                feed_match = re.search(r'(?i)feedback:\s*"([^"]+)"', right_side)
                if feed_match:
                    feedback = feed_match.group(1).strip()
                else:
                    feed_match = re.search(r"(?i)feedback:\s*'([^']+)'", right_side)
                    if feed_match:
                        feedback = feed_match.group(1).strip()

                next_state = right_side.split("(")[0].split("[")[0].strip()
                transitions.append(
                    Transition(condition=cond, next=next_state, feedback=feedback, require_approval=require_approval)
                )
    return transitions


def parse_transitions(body_lines: List[str]) -> List[Transition]:
    table_lines = [l for l in body_lines if "|" in l]
    if len(table_lines) >= 2:
        return parse_markdown_table_transitions(body_lines)
    return parse_markdown_list_transitions(body_lines)


# ---------------------------------------------------------------------------
# 3. 状态体解析
# ---------------------------------------------------------------------------


def _parse_output_schema(body_text: str) -> Optional[Dict[str, Any]]:
    json_text = body_text
    if json_text.startswith("```"):
        lines = json_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        json_text = "\n".join(lines).strip()
    try:
        return json.loads(json_text)
    except Exception as e:
        print(f"Warning: Failed to parse Output schema: {e}", file=sys.stderr)
        return None


def parse_state_body(state_name: str, body_text: str) -> StateInfo:
    """解析单个 `# [State]` 的状态体，返回 StateInfo。"""
    lines = body_text.splitlines()

    metadata: Dict[str, Any] = {
        "type": "llm",
        "tools": [],
        "src": None,
        "interactive": False,
        "is_final": False,
        "history_window": None,
        "max_loops": None,
    }

    instructions_lines: List[str] = []
    transitions: List[Transition] = []
    output_schema: Optional[Dict[str, Any]] = None

    mode = "meta"  # meta | instructions | sub_section
    current_sub_sec: Optional[str] = None
    current_sub_body: List[str] = []

    for line in lines:
        stripped = line.strip()

        sub_match = re.match(r"^##\s+\[([^\]]+)\]\s*$", line)
        if sub_match:
            if current_sub_sec:
                sec_name = current_sub_sec.lower()
                if sec_name == "transitions":
                    transitions.extend(parse_transitions(current_sub_body))
                elif sec_name in ("output json", "output schema", "output"):
                    output_schema = _parse_output_schema("\n".join(current_sub_body))
            current_sub_sec = sub_match.group(1).strip()
            current_sub_body = []
            mode = "sub_section"
            continue

        if mode == "meta":
            if stripped.startswith(("-", "*", "+")):
                item = stripped[1:].strip()
                if ":" in item:
                    k, v = item.split(":", 1)
                    k = k.strip().replace("**", "").lower()
                    v = v.strip().strip('"').strip("'")
                    if k == "type":
                        metadata["type"] = v.lower()
                    elif k == "tools":
                        metadata["tools"] = [t.strip() for t in v.split(",") if t.strip()]
                    elif k == "src":
                        metadata["src"] = v
                    elif k == "interactive":
                        metadata["interactive"] = v.lower() == "true"
                    elif k == "is_final":
                        metadata["is_final"] = v.lower() == "true"
                    elif k == "history_window":
                        try:
                            metadata["history_window"] = int(v)
                        except ValueError:
                            metadata["history_window"] = None
                    elif k == "max_loops":
                        try:
                            metadata["max_loops"] = int(v)
                        except ValueError:
                            metadata["max_loops"] = None
                continue
            elif not stripped:
                continue
            else:
                mode = "instructions"
                instructions_lines.append(line)

        elif mode == "instructions":
            instructions_lines.append(line)

        elif mode == "sub_section":
            current_sub_body.append(line)

    if current_sub_sec:
        sec_name = current_sub_sec.lower()
        if sec_name == "transitions":
            transitions.extend(parse_transitions(current_sub_body))
        elif sec_name in ("output json", "output schema", "output"):
            output_schema = _parse_output_schema("\n".join(current_sub_body))

    return StateInfo(
        name=state_name,
        instructions="\n".join(instructions_lines).strip(),
        transitions=transitions,
        is_final=metadata["is_final"],
        state_type=metadata["type"],
        tools=metadata["tools"],
        src=metadata["src"],
        interactive=metadata["interactive"],
        output_schema=output_schema,
        history_window=metadata["history_window"],
        max_loops=metadata["max_loops"],
    )


# ---------------------------------------------------------------------------
# 4. 顶层文档解析
# ---------------------------------------------------------------------------


def _register_io_options(data: Dict[str, Any], input_options: List[InputOption]) -> None:
    """从 Config/IO section 的 reader/writer 生成保留参数。"""
    reader = data.get("reader")
    writer = data.get("writer")

    if reader and not any(o.name in (RESERVED_INPUT, "input") for o in input_options):
        input_options.append(
            InputOption(
                name=RESERVED_INPUT,
                help="Input file path. If '-' or not provided, reads from stdin.",
                reader=reader,
            )
        )
    if writer and not any(o.name in (RESERVED_OUTPUT, "output") for o in input_options):
        input_options.append(
            InputOption(
                name=RESERVED_OUTPUT,
                help="Output file path. If '-' or not provided, writes to stdout.",
                writer=writer,
            )
        )


def _apply_sequential_fallback(state_infos: List[StateInfo]) -> None:
    """非 final 且无 transitions 的状态，自动连接声明顺序的下一个状态。"""
    for i, info in enumerate(state_infos):
        if not info.transitions and not info.is_final and i + 1 < len(state_infos):
            info.transitions.append(Transition(next=state_infos[i + 1].name))


def parse_compiled_skill(filepath: str, strict: bool = False) -> CompiledSkill:
    """解析 skill 文件为 CompiledSkill（IR）。

    strict=True 时，未知 section 由 warning 升级为 ParseError（可关闭的容错策略）。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    content = "".join(lines)

    # 拆分顶层 section
    pattern = r"^#\s+\[([^\]]+)\]\s*(.*)$"
    sections: List[Tuple[Tuple[str, str], str]] = []
    current_section: Optional[Tuple[str, str]] = None
    current_body: List[str] = []
    global_body: List[str] = []
    first_sec_found = False

    for line in content.splitlines():
        match = re.match(pattern, line)
        if match:
            first_sec_found = True
            if current_section:
                sections.append((current_section, "\n".join(current_body)))
            current_section = (match.group(1).strip(), match.group(2).strip())
            current_body = []
        else:
            if not first_sec_found:
                global_body.append(line)
            else:
                current_body.append(line)
    if current_section:
        sections.append((current_section, "\n".join(current_body)))

    compiled = CompiledSkill(global_text="\n".join(global_body).strip())
    state_infos: List[StateInfo] = []

    for (sec_type, sec_arg), body in sections:
        sec = sec_type.lower()

        if sec in (_SECTION_CONFIG, _SECTION_IO):
            data = parse_nested_markdown_list(body)
            if sec == _SECTION_CONFIG and "max_loops" in data:
                try:
                    compiled.max_loops = int(data["max_loops"])
                except ValueError:
                    pass
            _register_io_options(data, compiled.input_options)

        elif sec == _SECTION_TOOLS:
            tools_data = parse_nested_markdown_list(body)
            for name, val in tools_data.items():
                if isinstance(val, dict):
                    compiled.tools[name] = ToolInfo(
                        name=name,
                        type=str(val.get("type", "script")).lower(),
                        src=val.get("src"),
                        url=val.get("url"),
                        method=str(val.get("method", "GET")).upper(),
                        description=val.get("description", ""),
                    )

        elif sec == _SECTION_STATE:
            if not sec_arg:
                raise ParseError(f"State section missing name: `# [{sec_type}]`")
            state_infos.append(parse_state_body(sec_arg, body))

        else:
            if strict:
                raise ParseError(f"Unknown section `# [{sec_type}]` (strict mode enabled).")
            compiled.warnings.append(f"Unknown section `# [{sec_type}]` (preserved as natural language)")

    # 重复状态名 -> error
    names = [s.name for s in state_infos]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ParseError(f"Duplicate state names: {dupes}")

    # 隐式顺序 fallback（结构化归一化）
    _apply_sequential_fallback(state_infos)
    for info in state_infos:
        compiled.states[info.name] = info

    return compiled


def validate_state_graph(state_dict: Dict[str, StateInfo]) -> List[str]:
    """静态校验状态图（语义分析）。"""
    errors: List[str] = []
    state_names = list(state_dict.keys())
    if not state_names:
        return errors

    for i, (name, info) in enumerate(state_dict.items()):
        if not info.is_final and not info.transitions:
            if i + 1 >= len(state_names):
                errors.append(
                    f"Non-final state '{name}' is the last state but is missing a "
                    "'## [Transitions]' definition or 'is_final: true'."
                )
        for t in info.transitions:
            target = t.next.strip()
            if target.lower() in ("end", "finish"):
                continue
            if target not in state_dict:
                errors.append(f"State '{name}' has transition targeting non-existent state '{target}'.")
    return errors
