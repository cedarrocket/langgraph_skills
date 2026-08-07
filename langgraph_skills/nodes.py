"""节点工厂与通用路由器。

对应 PROCESS.md 设计基线的"节点工厂 + 路由"层：
  - create_node：为每个状态生成 LangGraph 节点函数（loop 计数、JSON 自愈校验、审批门在节点级完成）
  - generic_router / tool_router：ReAct 闭环与跨节点跳转路由

依赖方向：nodes -> models / executors / tools；不依赖 graph / runner / parser。
safe_input / run_skill 通过参数注入，避免循环依赖（由 runner 在构造时传入）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END

from langgraph_skills.executors import ExecutorContext, get_executor
from langgraph_skills.models import AgentState, NodeInfo
from langgraph_skills.tools import ToolRegistry

SafeInputFn = Callable[[str], str]
RunSkillFn = Callable[..., Dict[str, Any]]


def create_node(
    node_info: NodeInfo,
    tools: ToolRegistry,
    global_instructions: str = "",
    safe_input: Optional[SafeInputFn] = None,
    run_skill: Optional[RunSkillFn] = None,
):
    """动态生成通用的 LangGraph 节点处理函数。

    节点级通用逻辑（loop 计数、JSON 校验、人工审批门）在此；
    状态类型逻辑委托给 executors.EXECUTOR_REGISTRY（可插拔）。

    safe_input / run_skill 由调用方（graph.build_graph）注入，避免 nodes 反向依赖 runner。
    """

    def node_function(state: AgentState):
        current_loops = state.get("loop_count", 0) + 1
        current_max_loops = state.get("max_loops", 10)
        new_max_loops = max(current_max_loops, 20) if node_info.interactive else current_max_loops

        deliverables = state.get("deliverables", {})
        next_state = None
        output_messages: Optional[List[BaseMessage]] = None

        print(f"\n--- [Node: {node_info.name}] Execution (Loop {current_loops}/{new_max_loops}) [Type: {node_info.node_type.capitalize()}] ---")

        executor = get_executor(node_info.node_type)
        if executor is None:
            raise ValueError(
                f"Unknown state type '{node_info.node_type}' for state '{node_info.name}'. No executor registered."
            )

        if safe_input is None or run_skill is None:
            raise RuntimeError(
                f"Node '{node_info.name}': safe_input/run_skill must be injected by graph.build_graph / runner."
            )

        ctx = ExecutorContext(
            node_info=node_info,
            state=state,
            tools=tools,
            safe_input=safe_input,
            run_skill=run_skill,
        )
        result = executor(ctx)
        next_state = result.next_state
        output_messages = result.output_messages
        if result.payload is not None:
            deliverables["payload"] = result.payload

        # JSON Schema 校验与人工审批门 (通用逻辑)
        if next_state and next_state != node_info.name and not node_info.is_final:
            if node_info.output_schema and deliverables.get("payload"):
                raw_payload = deliverables["payload"].strip()
                if raw_payload.startswith("```"):
                    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_payload, re.DOTALL)
                    if match:
                        raw_payload = match.group(1).strip()
                try:
                    payload_data = json.loads(raw_payload)
                    from jsonschema import ValidationError, validate  # type: ignore[import-untyped]

                    validate(instance=payload_data, schema=node_info.output_schema)
                    print("  [JSON Validation] Payload matches output schema.")
                    deliverables["payload"] = json.dumps(payload_data, ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    print("  [JSON Validation Failed] Payload is not valid JSON. Routing back for self-healing.")
                    next_state = node_info.name
                    deliverables["payload"] = (
                        f"JSON validation failed: Output must be a valid JSON string. Your previous output was:\n"
                        f"{deliverables['payload']}\nPlease format it correctly."
                    )
                except ValidationError as ve:
                    print(f"  [JSON Validation Failed] Schema mismatch: {ve.message}. Routing back for self-healing.")
                    next_state = node_info.name
                    deliverables["payload"] = (
                        f"JSON validation failed against schema: {ve.message}.\n"
                        f"Your previous output was:\n{deliverables['payload']}\nPlease correct the structure."
                    )

            if next_state and next_state != node_info.name:
                matching_trans = next((t for t in node_info.transitions if t.next == next_state), None)
                if matching_trans and matching_trans.require_approval:
                    print(f"\n[Approval Required] Transition from '{node_info.name}' to '{next_state}' requires approval.")
                    print("--- Payload Content ---")
                    print(deliverables.get("payload", ""))
                    print("-----------------------")
                    user_app = safe_input("Approve? (y / n / [enter feedback to reject and revise]): ").strip()
                    if user_app.lower() == "y":
                        print("  [Approved] Proceeding to next state.")
                    else:
                        feedback_msg = user_app if (user_app.lower() != "n" and user_app) else "Rejected by user."
                        print(f"  [Rejected] Routing back to '{node_info.name}' for revision. Feedback: {feedback_msg}")
                        next_state = node_info.name
                        deliverables["payload"] = (
                            f"Your transition to '{matching_trans.next}' was rejected by the user with feedback:\n"
                            f"{feedback_msg}\nPlease revise the output."
                        )

        ret: Dict[str, Any] = {
            "next_state": next_state,
            "deliverables": deliverables,
            "loop_count": current_loops,
            "max_loops": new_max_loops,
            "current_node": node_info.name,
        }
        if output_messages is not None:
            ret["messages"] = output_messages
        return ret

    return node_function


def generic_router(state: AgentState):
    """核心通用路由：智能区分工具调用（ReAct）和跨节点状态跳转。"""
    loop_count = state.get("loop_count", 0)
    max_loops = state.get("max_loops", 10)

    if loop_count >= max_loops:
        print(f"\n[Warning]: Loop limit of {max_loops} reached! Forcing END.")
        return END

    # 获取最后一条消息，判断是否是工具调用
    messages = state.get("messages", [])
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            # 排除 SubmitResult，如果是其他普通工具，就路由到 ToolNode 执行
            for call in last_msg.tool_calls:
                if call["name"] != "SubmitResult":
                    return "tools"

    # 如果是跳转或者普通结束，返回 next_state 节点名
    return state.get("next_state") or END


def tool_router(state: AgentState):
    """ToolNode 执行完毕后，通用路由回原来的触发节点，形成 ReAct 闭环。"""
    return state.get("current_node") or END
