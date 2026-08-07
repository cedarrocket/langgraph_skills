"""IR 数据模型（与 AST 合一，见 PROCESS.md 设计基线）。

本模块只定义数据，不含解析或执行逻辑：
- AgentState：LangGraph 图上流动的运行时状态
- CompiledSkill / NodeInfo / Transition / ToolInfo / InputOption：
  parser 的输出（IR），同时也是运行时模型

序列化规则（金标准测试 / dump_ir.py 共用 compiled_to_dict / node_to_dict）：
JSON 快照 = dataclass 的 asdict；warnings 是运行时诊断，不入快照。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages

# 状态类型常量
NODE_LLM = "llm"
NODE_CODE = "code"
NODE_SCRIPT = "script"
NODE_SKILL = "skill"

# 工具类型常量
TOOL_SCRIPT = "script"
TOOL_API = "api"

# 保留参数名
RESERVED_INPUT = "input_path"
RESERVED_OUTPUT = "output_path"

# 默认值
DEFAULT_MAX_LOOPS = 10


class ReplaceMessages:
    """整体替换消息列表的标记类型（==> X <== 覆盖语义）。

    子图后处理节点返回 messages: ReplaceMessages([...])，
    自定义 reducer 检测到该类型即整体替换（而非 add_messages 合并）。
    """

    def __init__(self, messages: List[Any]):
        self.messages = list(messages)


def messages_reducer(current: Optional[list], incoming: Any) -> list:
    """messages channel 的 reducer。

    - 普通消息列表 → add_messages 合并（默认行为）
    - ReplaceMessages 包裹 → 整体替换（==> X <== 覆盖语义）
    """
    if isinstance(incoming, ReplaceMessages):
        return incoming.messages
    merged = add_messages(current or [], incoming)
    return list(merged) if not isinstance(merged, list) else merged


def merge_dicts(current: Optional[dict], incoming: Any) -> dict:
    """deliverables channel 的 reducer：字段级字典合并（fan-in 合并规则）。

    并行分支（fan-out）各写独立 key，join 节点收到合并后的完整字典；
    同 key 多分支写入时后到覆盖（文档化语义，避免单值冲突报错）。
    """
    merged = dict(current or {})
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def last_wins(current: Any, incoming: Any) -> Any:
    """单值 channel 的 reducer：后到覆盖（fan-in 时多分支写同值 key 不冲突）。"""
    return incoming


class AgentState(TypedDict):
    """LangGraph 图上流动的状态。

    `messages` 由 messages_reducer 累积（支持整体替换），
    `deliverables` 由 merge_dicts 字段级合并（支持 fan-in 并行合并），
    其余字段作为节点间传递的上下文。
    """

    messages: Annotated[list, messages_reducer]
    global_instructions: str
    state_instructions: str
    deliverables: Annotated[dict, merge_dicts]
    current_node: Annotated[str, last_wins]
    next_state: Annotated[str, last_wins]
    loop_count: Annotated[int, last_wins]
    max_loops: Annotated[int, last_wins]


@dataclass
class Transition:
    """单个状态跳转规则。"""

    condition: Optional[str] = None  # 条件；None 表示无条件（Default）
    next: str = ""  # 目标状态名
    parallel: bool = False  # True：fan-out 并行目标（多个 parallel=True 的 Transition 组成一组并行分支）
    feedback: Optional[str] = None  # 跳转时回传给目标状态的反馈
    require_approval: bool = False  # 是否需人工审批
    inherit_history: bool = False  # True（==>）：目标节点继承源节点的消息历史；False（->）：不继承（现状）
    replace_messages: bool = False  # True（==>X<==）：调用子图时输出整体覆盖父图 messages（压缩等替换场景）


@dataclass
class NodeInfo:
    """单个状态节点的声明式描述。"""

    name: str
    instructions: str = ""
    transitions: List[Transition] = field(default_factory=list)
    is_final: bool = False
    node_type: str = NODE_LLM
    subgraph: Optional["SubGraphInfo"] = None  # node_type=="subgraph" 时指向嵌套子图
    tools: List[str] = field(default_factory=list)
    src: Optional[str] = None  # script / skill 的路径
    interactive: bool = False
    output_schema: Optional[Dict[str, Any]] = None
    history_window: Optional[int] = None
    max_loops: Optional[int] = None  # None 表示继承全局 max_loops
    max_context_length: Optional[int] = None  # pre_node 检查点：上下文超过该值时提前 return 跳转（去压缩子图）
    triggers: List[Dict[str, Any]] = field(default_factory=list)  # 节点级 trigger 配置（dict 形式）

    @property
    def is_script(self) -> bool:
        return self.node_type == NODE_SCRIPT

    @property
    def is_code(self) -> bool:
        return self.node_type == NODE_CODE

    @property
    def is_skill(self) -> bool:
        return self.node_type == NODE_SKILL

    @property
    def is_llm(self) -> bool:
        return self.node_type == NODE_LLM


@dataclass
class ToolInfo:
    """Skill 中 `# [Tools]` 声明的工具。"""

    name: str
    type: str = TOOL_SCRIPT  # script | api
    src: Optional[str] = None
    url: Optional[str] = None
    method: str = "GET"
    description: str = ""


@dataclass
class InputOption:
    """`# [IO]` 生成的保留参数（input_path / output_path）。"""

    name: str
    required: bool = False
    default: Any = None
    help: str = ""
    env: Optional[str] = None
    reader: Optional[str] = None  # 读入工具名
    writer: Optional[str] = None  # 写出工具名


@dataclass
class SubGraphInfo:
    """子图声明（`# [SubGraph] Name`）。

    两种形态：
      - nodes: 子图体 = 内部节点列表（形态 A，真子图）
      - src:   子图体只有一行 `- **src**: path`，加载外部 skill 文件
    """

    name: str
    nodes: Dict[str, NodeInfo] = field(default_factory=dict)
    src: Optional[str] = None  # 外部 skill 文件路径（src 简写形态）
    warnings: List[str] = field(default_factory=list)


@dataclass
class CompiledSkill:
    """`parse_compiled_skill` 的完整解析产物（IR）。"""

    global_text: str = ""
    nodes: Dict[str, NodeInfo] = field(default_factory=dict)
    subgraphs: Dict[str, SubGraphInfo] = field(default_factory=dict)
    max_loops: int = DEFAULT_MAX_LOOPS
    tools: Dict[str, ToolInfo] = field(default_factory=dict)
    input_options: List[InputOption] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    triggers: List[Dict[str, Any]] = field(default_factory=list)  # 全局 trigger 配置（dict 形式）

    @property
    def start_node(self) -> Optional[str]:
        return next(iter(self.nodes), None)

    @property
    def is_empty(self) -> bool:
        return not self.nodes


# ---------------------------------------------------------------------------
# 序列化（金标准快照）
# ---------------------------------------------------------------------------


def node_to_dict(s: NodeInfo) -> dict:
    return {
        "name": s.name,
        "instructions": s.instructions,
        "transitions": [asdict(t) for t in s.transitions],
        "is_final": s.is_final,
        "node_type": s.node_type,
        "tools": s.tools,
        "src": s.src,
        "interactive": s.interactive,
        "output_schema": s.output_schema,
        "history_window": s.history_window,
        "max_loops": s.max_loops,
    }


def compiled_to_dict(skill: CompiledSkill) -> dict:
    return {
        "global_text": skill.global_text,
        "max_loops": skill.max_loops,
        "nodes": {name: node_to_dict(info) for name, info in skill.nodes.items()},
        "tools": {name: asdict(t) for name, t in skill.tools.items()},
        "input_options": [asdict(o) for o in skill.input_options],
    }
