"""节点执行器（可插拔）。

对应 PROCESS.md 设计基线的"执行器"层：
  - 每个 node_type（llm / code / script / skill）对应一个执行器
  - EXECUTOR_REGISTRY：类型 -> 执行器工厂；通过 register_executor 可扩展新状态类型
  - 沙箱/环境扩展点：ExecutorContext 携带 config，将来可注入沙箱、权限等

依赖方向：executors -> models / tools；不依赖 interpreter / parser。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from langgraph_skills.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    Settings,
    get_deepseek_key,
)
from langgraph_skills.models import NODE_CODE, NODE_LLM, NODE_SCRIPT, NODE_SKILL, AgentState, NodeInfo
from langgraph_skills.tools import ToolRegistry

# ---------------------------------------------------------------------------
# 执行上下文与结果
# ---------------------------------------------------------------------------


@dataclass
class ExecutorContext:
    """执行器需要的全部外部依赖，集中注入以便测试与未来沙箱化。"""

    node_info: NodeInfo
    state: AgentState
    tools: ToolRegistry  # 当前图的工具注册表
    safe_input: Callable[[str], str]
    run_skill: Callable[..., Dict[str, Any]]  # 嵌套 skill 的入口
    config: Dict[str, Any] = field(default_factory=dict)  # 沙箱/环境扩展点
    settings: Settings = field(default_factory=Settings.from_env)
    model: str = field(default=DEFAULT_MODEL)
    base_url: str = field(default=DEFAULT_BASE_URL)
    temperature: float = field(default=DEFAULT_TEMPERATURE)


@dataclass
class ExecutorResult:
    """执行器输出；node 层在此基础上做通用后处理（JSON 校验/审批门）。"""

    next_state: Optional[str] = None
    payload: Optional[str] = None
    output_messages: Optional[List[BaseMessage]] = None


# ---------------------------------------------------------------------------
# 内置执行器
# ---------------------------------------------------------------------------


def _strip_code_fence(code_str: str) -> str:
    if code_str.startswith("```python"):
        code_str = code_str[9:]
    if code_str.startswith("```"):
        code_str = code_str[3:]
    if code_str.endswith("```"):
        code_str = code_str[:-3]
    return code_str


def execute_code(ctx: ExecutorContext) -> ExecutorResult:
    """type=code：执行状态体内的内联 Python。"""
    info = ctx.node_info
    code_str = _strip_code_fence(info.instructions)

    local_outputs: Dict[str, Optional[str]] = {"next_state": None, "payload": None}

    def transition_to(next_state_name: str, payload_data: str) -> None:
        local_outputs["next_state"] = next_state_name
        local_outputs["payload"] = payload_data

    local_vars = {
        "deliverables": ctx.state["deliverables"],
        "messages": ctx.state["messages"],
        "get_payload": lambda: ctx.state["deliverables"].get("payload"),
        "transition_to": transition_to,
    }

    try:
        exec(code_str.strip(), {}, local_vars)
    except Exception as e:
        print(f"Error executing Python code: {e}")
        raise e

    return ExecutorResult(next_state=local_outputs["next_state"], payload=local_outputs["payload"])


def execute_script(ctx: ExecutorContext) -> ExecutorResult:
    """type=script：执行 src 指向的外部 Python 文件。"""
    info = ctx.node_info
    script_path = info.src
    if not script_path:
        raise ValueError(f"Script state '{info.name}' is missing the 'src' attribute.")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script file '{script_path}' not found for state '{info.name}'.")

    with open(script_path, "r", encoding="utf-8") as f:
        code_str = f.read()

    local_outputs: Dict[str, Optional[str]] = {"next_state": None, "payload": None}

    def transition_to(next_state_name: str, payload_data: str) -> None:
        local_outputs["next_state"] = next_state_name
        local_outputs["payload"] = payload_data

    local_vars = {
        "deliverables": ctx.state["deliverables"],
        "messages": ctx.state["messages"],
        "get_payload": lambda: ctx.state["deliverables"].get("payload"),
        "transition_to": transition_to,
    }

    try:
        exec(code_str.strip(), {}, local_vars)
    except Exception as e:
        print(f"Error executing script: {e}")
        raise e

    return ExecutorResult(next_state=local_outputs["next_state"], payload=local_outputs["payload"])


def execute_skill(ctx: ExecutorContext) -> ExecutorResult:
    """type=skill：运行嵌套子 skill，payload 作为输入。"""
    info = ctx.node_info
    child_skill_path = info.src
    if not child_skill_path:
        raise ValueError(f"Skill state '{info.name}' is missing the 'src' attribute.")
    if not os.path.exists(child_skill_path):
        raise FileNotFoundError(f"Child skill file '{child_skill_path}' not found for state '{info.name}'.")

    parent_payload = ctx.state["deliverables"].get("payload", "")
    child_deliverables = ctx.run_skill(
        child_skill_path,
        user_input=f"Parent payload context: {parent_payload}",
        initial_deliverables={"payload": parent_payload},
    )

    next_state = info.transitions[0].next if info.transitions else None
    return ExecutorResult(next_state=next_state, payload=child_deliverables.get("payload", ""))


def execute_llm(ctx: ExecutorContext) -> ExecutorResult:
    """type=llm：构造 prompt、绑定工具、调用 LLM、处理 SubmitResult / 交互。"""
    info = ctx.node_info
    state = ctx.state
    deliverables = state.get("deliverables", {})

    api_key = ctx.settings.api_key or get_deepseek_key()
    llm = ChatOpenAI(
        model=ctx.model,
        temperature=ctx.temperature,
        api_key=SecretStr(api_key) if api_key else None,
        base_url=ctx.base_url,
    )

    has_conditional_transitions = any(t.condition for t in info.transitions)
    need_submit_result = (not info.is_final) and (has_conditional_transitions or len(info.transitions) > 1)

    # SubmitResult 工具（内联定义，感知当前状态的合法跳转）
    from pydantic import BaseModel, Field

    class SubmitResult(BaseModel):
        """Submit the result of this stage and transition to the next stage."""

        next_state: Optional[str] = Field(
            default=info.transitions[0].next if len(info.transitions) == 1 else None,
            description=f"The next state to transition to. Valid options: {[t.next for t in info.transitions]}",
        )
        payload: str = Field(description="The deliverable data or feedback to pass to the next stage")

    bound_tools: List[Any] = []
    if need_submit_result:
        bound_tools.append(SubmitResult)
    bound_tools.extend(ctx.tools.resolve_many(info.tools))

    if bound_tools:
        force_tool = need_submit_result and (not info.interactive)
        llm_with_tools = llm.bind_tools(bound_tools, tool_choice="required" if force_tool else None)
    else:
        llm_with_tools = llm

    # 构造系统提示词
    sys_prompt = f"{(state.get('global_instructions') or '').strip()}\n\n[Current Task: {info.name}]\n{info.instructions}"
    if info.output_schema:
        sys_prompt += (
            f"\n\n[Output JSON Schema Constraint]:\nYou MUST output a valid JSON object that strictly "
            f"matches this JSON Schema:\n```json\n{json.dumps(info.output_schema, indent=2, ensure_ascii=False)}\n```"
        )
    if state["deliverables"]:
        params = {k: v for k, v in state["deliverables"].items() if k not in ("feedback", "start_msg_index", "exit_code", "payload")}
        if params:
            sys_prompt += "\n\n[Current Deliverables & CLI Parameters]:"
            for k, v in params.items():
                if isinstance(v, str) and len(v) > 80:
                    sys_prompt += f"\n- {k}:\n```\n{v}\n```"
                else:
                    sys_prompt += f"\n- {k}: {v}"
        if state["deliverables"].get("payload"):
            sys_prompt += f"\n\n[Context from previous stage]:\n{state['deliverables'].get('payload')}"
        if state["deliverables"].get("feedback"):
            sys_prompt += f"\n\n[Feedback from transition]:\n{state['deliverables'].get('feedback')}"

    if not info.is_final:
        valid_nexts = [t.next for t in info.transitions]
        if need_submit_result:
            sys_prompt += (
                f"\n\n[System Directive]: When your task is complete, call `SubmitResult` to transition. "
                f"Valid options: {list(set(valid_nexts))}."
            )

    # 历史窗口
    if "start_msg_index" not in state["deliverables"]:
        state["deliverables"]["start_msg_index"] = 0
        current_node_messages = state["messages"]
    elif info.name != state["current_node"]:
        state["deliverables"]["start_msg_index"] = len(state["messages"])
        current_node_messages = []
    else:
        start_idx = state["deliverables"]["start_msg_index"]
        current_node_messages = state["messages"][start_idx:]

    if info.history_window is not None:
        human_indices = [i for i, m in enumerate(current_node_messages) if isinstance(m, HumanMessage)]
        if len(human_indices) > info.history_window:
            slice_idx = human_indices[-info.history_window]
            current_node_messages = current_node_messages[slice_idx:]

    messages = [HumanMessage(content=sys_prompt)] + current_node_messages
    response = llm_with_tools.invoke(messages)

    out_msgs: List[BaseMessage] = [response]
    next_state = None
    triggered_transition = False
    payload = None

    if response.tool_calls:
        for tool_call in response.tool_calls:
            if tool_call["name"] == "SubmitResult":
                next_state = tool_call["args"].get("next_state")
                if not next_state and info.transitions:
                    next_state = info.transitions[0].next
                payload = tool_call["args"].get("payload", "")
                matching_trans = next((t for t in info.transitions if t.next == next_state), None)
                if matching_trans and matching_trans.feedback:
                    deliverables["feedback"] = matching_trans.feedback
                else:
                    deliverables["feedback"] = None
                print(f"  -> Model triggered transition: {next_state}")
                from langchain_core.messages import ToolMessage

                out_msgs.append(
                    ToolMessage(content="Transition approved.", tool_call_id=tool_call["id"], name="SubmitResult")
                )
                triggered_transition = True

    if not triggered_transition and not info.is_final:
        if not need_submit_result and info.transitions:
            next_state = info.transitions[0].next
            payload = response.content
            print(f"  -> Auto-transition to: {next_state} (unconditional)")
            triggered_transition = True

    if info.interactive and not triggered_transition:
        print(f"\nAI: {response.content}\n")
        user_input = ctx.safe_input("You: ")
        out_msgs.append(HumanMessage(content=user_input))
        next_state = info.name

    return ExecutorResult(next_state=next_state, payload=payload, output_messages=out_msgs)


# ---------------------------------------------------------------------------
# 执行器注册表（可插拔）
# ---------------------------------------------------------------------------

ExecutorFn = Callable[[ExecutorContext], ExecutorResult]

EXECUTOR_REGISTRY: Dict[str, ExecutorFn] = {
    NODE_LLM: execute_llm,
    NODE_CODE: execute_code,
    NODE_SCRIPT: execute_script,
    NODE_SKILL: execute_skill,
}


def register_executor(node_type: str, fn: ExecutorFn) -> None:
    """注册新的状态类型执行器（扩展点）。"""
    EXECUTOR_REGISTRY[node_type] = fn


def get_executor(node_type: str) -> Optional[ExecutorFn]:
    return EXECUTOR_REGISTRY.get(node_type)
