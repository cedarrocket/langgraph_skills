"""IR 数据模型（与 AST 合一，见 PROCESS.md 设计基线）。

本模块只定义数据，不含解析或执行逻辑：
- AgentState：LangGraph 图上流动的运行时状态
- CompiledSkill / StateInfo / Transition / ToolInfo / InputOption：
  parser 的输出（IR），同时也是运行时模型

序列化规则（金标准测试 / dump_ir.py 共用 compiled_to_dict / state_to_dict）：
JSON 快照 = dataclass 的 asdict；warnings 是运行时诊断，不入快照。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages

# 状态类型常量
STATE_LLM = "llm"
STATE_CODE = "code"
STATE_SCRIPT = "script"
STATE_SKILL = "skill"

# 工具类型常量
TOOL_SCRIPT = "script"
TOOL_API = "api"

# 保留参数名
RESERVED_INPUT = "input_path"
RESERVED_OUTPUT = "output_path"

# 默认值
DEFAULT_MAX_LOOPS = 10


class AgentState(TypedDict):
    """LangGraph 图上流动的状态。

    `messages` 由 LangGraph 的 add_messages reducer 累积，
    其余字段作为节点间传递的上下文。
    """

    messages: Annotated[list, add_messages]
    global_instructions: str
    state_instructions: str
    deliverables: dict
    current_node: str
    next_state: str
    loop_count: int
    max_loops: int


@dataclass
class Transition:
    """单个状态跳转规则。"""

    condition: Optional[str] = None  # 条件；None 表示无条件（Default）
    next: str = ""  # 目标状态名
    feedback: Optional[str] = None  # 跳转时回传给目标状态的反馈
    require_approval: bool = False  # 是否需人工审批


@dataclass
class StateInfo:
    """单个状态节点的声明式描述。"""

    name: str
    instructions: str = ""
    transitions: List[Transition] = field(default_factory=list)
    is_final: bool = False
    state_type: str = STATE_LLM
    tools: List[str] = field(default_factory=list)
    src: Optional[str] = None  # script / skill 的路径
    interactive: bool = False
    output_schema: Optional[Dict[str, Any]] = None
    history_window: Optional[int] = None
    max_loops: Optional[int] = None  # None 表示继承全局 max_loops

    @property
    def is_script(self) -> bool:
        return self.state_type == STATE_SCRIPT

    @property
    def is_code(self) -> bool:
        return self.state_type == STATE_CODE

    @property
    def is_skill(self) -> bool:
        return self.state_type == STATE_SKILL

    @property
    def is_llm(self) -> bool:
        return self.state_type == STATE_LLM


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
class CompiledSkill:
    """`parse_compiled_skill` 的完整解析产物（IR）。"""

    global_text: str = ""
    states: Dict[str, StateInfo] = field(default_factory=dict)
    max_loops: int = DEFAULT_MAX_LOOPS
    tools: Dict[str, ToolInfo] = field(default_factory=dict)
    input_options: List[InputOption] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def start_state(self) -> Optional[str]:
        return next(iter(self.states), None)

    @property
    def is_empty(self) -> bool:
        return not self.states


# ---------------------------------------------------------------------------
# 序列化（金标准快照）
# ---------------------------------------------------------------------------


def state_to_dict(s: StateInfo) -> dict:
    return {
        "name": s.name,
        "instructions": s.instructions,
        "transitions": [asdict(t) for t in s.transitions],
        "is_final": s.is_final,
        "state_type": s.state_type,
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
        "states": {name: state_to_dict(info) for name, info in skill.states.items()},
        "tools": {name: asdict(t) for name, t in skill.tools.items()},
        "input_options": [asdict(o) for o in skill.input_options],
    }
