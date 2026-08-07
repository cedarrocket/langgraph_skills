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
    NodeInfo,
    SubGraphInfo,
    ToolInfo,
    Transition,
)

# 顶层 section 类型（key 转小写后匹配）
_SECTION_CONFIG = "config"
_SECTION_IO = "io"
_SECTION_TOOLS = "tools"
_SECTION_STATE = "node"
_SECTION_SUBGRAPH = "subgraph"

_KNOWN_SECTIONS = {_SECTION_CONFIG, _SECTION_IO, _SECTION_TOOLS, _SECTION_STATE, _SECTION_SUBGRAPH}


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
        next_state = row_dict.get("next node") or row_dict.get("next state") or row_dict.get("next") or row_dict.get("next_state")
        if next_state:
            # 三态：==> X <==（继承+覆盖）/ ==> X（继承）/ X（不继承，默认）
            replace_messages = "<==" in next_state
            inherit_history = "==>" in next_state
            if replace_messages:
                next_state = next_state.replace("<==", "").strip()
            if inherit_history:
                next_state = next_state.replace("==>", "").strip()
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
                    inherit_history=inherit_history,
                    replace_messages=replace_messages,
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

            # 箭头三态：
            #   ==>X<== : 继承 + 覆盖（子图输出替换父图 messages）
            #   ==>X    : 继承（子图输出合并回父图）
            #   ->X     : 不继承（默认）
            replace_messages = "<==" in item
            arrow = "==>" if "==>" in item else "->"
            inherit_history = arrow == "==>"
            if arrow not in item:
                continue

            # Default ==> TargetState / Default -> TargetState
            if "default" in item.lower():
                right_side = item.split(arrow, 1)[1].strip()
                if replace_messages:
                    right_side = right_side.replace("<==", "").strip()
                require_approval = "[require approval]" in right_side.lower() or "(require approval)" in right_side.lower()
                next_state = right_side.split("(")[0].split("[")[0].strip()
                transitions.append(
                    Transition(
                        next=next_state,
                        require_approval=require_approval,
                        inherit_history=inherit_history,
                        replace_messages=replace_messages,
                    )
                )
                continue

            # If `cond` ==> TargetState (Feedback: "...") [Require Approval]
            if "if" in item.lower():
                cond_match = re.search(r"(?i)if\s+`([^`]+)`", item)
                if cond_match:
                    cond = cond_match.group(1).strip()
                else:
                    cond = item.split(arrow, 1)[0].replace("if", "", 1).strip()

                right_side = item.split(arrow, 1)[1].strip()
                if replace_messages:
                    right_side = right_side.replace("<==", "").strip()
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
                    Transition(
                        condition=cond,
                        next=next_state,
                        feedback=feedback,
                        require_approval=require_approval,
                        inherit_history=inherit_history,
                        replace_messages=replace_messages,
                    )
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


def parse_node_body(node_name: str, body_text: str) -> NodeInfo:
    """解析单个 `# [Node]` 的状态体，返回 NodeInfo。"""
    lines = body_text.splitlines()

    metadata: Dict[str, Any] = {
        "type": "llm",
        "tools": [],
        "src": None,
        "interactive": False,
        "is_final": False,
        "history_window": None,
        "max_loops": None,
        "max_context_length": None,
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
                    elif k == "max_context_length":
                        try:
                            metadata["max_context_length"] = int(v)
                        except ValueError:
                            metadata["max_context_length"] = None
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

    return NodeInfo(
        name=node_name,
        instructions="\n".join(instructions_lines).strip(),
        transitions=transitions,
        is_final=metadata["is_final"],
        node_type=metadata["type"],
        tools=metadata["tools"],
        src=metadata["src"],
        interactive=metadata["interactive"],
        output_schema=output_schema,
        history_window=metadata["history_window"],
        max_loops=metadata["max_loops"],
        max_context_length=metadata["max_context_length"],
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


def _apply_sequential_fallback(node_infos: List[NodeInfo]) -> None:
    """非 final 且无 transitions 的状态，自动连接声明顺序的下一个状态。"""
    for i, info in enumerate(node_infos):
        if not info.transitions and not info.is_final and i + 1 < len(node_infos):
            info.transitions.append(Transition(next=node_infos[i + 1].name))


def _split_sub_sections(body: str) -> List[Tuple[Tuple[str, str], str]]:
    """按 `## [X]` 切分子图体内的子区块（子图内节点用二级标题）。"""
    pattern = r"^##\s+\[([^\]]+)\]\s*(.*)$"
    sections: List[Tuple[Tuple[str, str], str]] = []
    current: Optional[Tuple[str, str]] = None
    current_body: List[str] = []
    for line in body.splitlines():
        match = re.match(pattern, line)
        if match:
            if current:
                sections.append((current, "\n".join(current_body)))
            current = (match.group(1).strip(), match.group(2).strip())
            current_body = []
        else:
            current_body.append(line)
    if current:
        sections.append((current, "\n".join(current_body)))
    return sections


def _parse_subgraph_body(name: str, body: str) -> SubGraphInfo:
    """解析 `# [SubGraph] Name` 的 body。

    两种形态：
      - body 只有 `- **src**: path` → 加载外部文件（src 简写）
      - body 含 `## [Node]` 子区块 → 形态 A（真子图，内部节点列表）
    """
    sub = SubGraphInfo(name=name)

    # 检查 src 简写形态
    src_match = re.search(r"(?m)^\s*-\s*\*\*src\*\*:\s*(.+)$", body)
    if src_match and "## [" not in body:
        sub.src = src_match.group(1).strip()
        return sub

    # 形态 A：解析子图体内的 ## [Node]
    node_infos: List[NodeInfo] = []
    for (sec_type, sec_arg), sec_body in _split_sub_sections(body):
        sec = sec_type.lower()
        if sec == _SECTION_STATE:
            if not sec_arg:
                raise ParseError(f"Node section missing name in subgraph '{name}': `## [{sec_type}]`")
            node_infos.append(parse_node_body(sec_arg, sec_body))
    _apply_sequential_fallback(node_infos)
    for info in node_infos:
        sub.nodes[info.name] = info
    return sub


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
    node_infos: List[NodeInfo] = []

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
                raise ParseError(f"Node section missing name: `# [{sec_type}]`")
            node_infos.append(parse_node_body(sec_arg, body))

        elif sec == _SECTION_SUBGRAPH:
            if not sec_arg:
                raise ParseError(f"SubGraph section missing name: `# [{sec_type}]`")
            sub = _parse_subgraph_body(sec_arg, body)
            if sub.name in compiled.subgraphs:
                raise ParseError(f"Duplicate subgraph name: '{sub.name}'")
            compiled.subgraphs[sub.name] = sub

        else:
            if strict:
                raise ParseError(f"Unknown section `# [{sec_type}]` (strict mode enabled).")
            compiled.warnings.append(f"Unknown section `# [{sec_type}]` (preserved as natural language)")

    # 重复状态名 -> error
    names = [s.name for s in node_infos]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ParseError(f"Duplicate state names: {dupes}")

    # 隐式顺序 fallback（结构化归一化）
    _apply_sequential_fallback(node_infos)
    for info in node_infos:
        compiled.nodes[info.name] = info

    # 子图调用检查：-> 调用子图（不继承）→ warning（不强制）
    for info in compiled.nodes.values():
        for t in info.transitions:
            if t.next in compiled.subgraphs and not t.inherit_history:
                compiled.warnings.append(
                    f"Node '{info.name}' transitions to subgraph '{t.next}' without `==>` "
                    "(no message history inheritance). Use `==>` or `==> <==` if the subgraph needs parent context."
                )

    return compiled


def validate_node_graph(node_dict: Dict[str, NodeInfo], subgraph_names: Optional[set] = None) -> List[str]:
    """静态校验节点图（语义分析）。

    subgraph_names：已声明的子图名集合；指向子图的 transition 不算悬空。
    """
    errors: List[str] = []
    subgraph_names = subgraph_names or set()
    node_names = list(node_dict.keys())
    if not node_names:
        return errors

    for i, (name, info) in enumerate(node_dict.items()):
        if not info.is_final and not info.transitions:
            if i + 1 >= len(node_names):
                errors.append(
                    f"Non-final node '{name}' is the last node but is missing a "
                    "'## [Transitions]' definition or 'is_final: true'."
                )
        for t in info.transitions:
            target = t.next.strip()
            if target.lower() in ("end", "finish"):
                continue
            if target in subgraph_names:
                continue  # 目标是子图节点，合法
            if target not in node_dict:
                errors.append(f"Node '{name}' has transition targeting non-existent node '{target}'.")
    return errors
