"""节点工厂与通用路由器。

对应 PROCESS.md 设计基线的"节点工厂 + 路由"层：
  - create_node：为每个状态生成 LangGraph 节点函数（loop 计数、JSON 自愈校验、审批门在节点级完成）
  - generic_router / tool_router：ReAct 闭环与跨节点跳转路由

依赖方向：nodes -> models / executors / tools；不依赖 graph / runner / parser。
safe_input / run_skill 通过参数注入，避免循环依赖（由 runner 在构造时传入）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END

from langgraph_skills.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    Settings,
)
from langgraph_skills.executors import ExecutorContext, get_executor
from langgraph_skills.models import AgentState, NodeHook, NodeInfo, OnCondition
from langgraph_skills.tools import ToolRegistry
from langgraph_skills.triggers import (
    CHECKPOINT_POST_NODE,
    Trigger,
    evaluate_condition,
    run_handler,
    triggers_for_checkpoint,
)

SafeInputFn = Callable[[str], str]
RunSkillFn = Callable[..., Dict[str, Any]]


def create_node(
    node_info: NodeInfo,
    tools: ToolRegistry,
    global_instructions: str = "",
    safe_input: Optional[SafeInputFn] = None,
    run_skill: Optional[RunSkillFn] = None,
    settings: Optional[Settings] = None,
    triggers: Optional[List[Trigger]] = None,
    subgraph_names: Optional[set] = None,
):
    """动态生成通用的 LangGraph 节点处理函数。

    节点级通用逻辑（loop 计数、JSON 校验、人工审批门、post_node trigger）在此；
    状态类型逻辑委托给 executors.EXECUTOR_REGISTRY（可插拔）。

    safe_input / run_skill / settings / triggers 由调用方（graph.build_graph）注入，
    避免 nodes 反向依赖 runner / config 加载逻辑。
    """

    def node_function(state: AgentState):
        deliverables = state.get("deliverables", {})
        msg_start_idx = len(state.get("messages", []))

        # pre_node 检查点：上下文超限 → 提前 return 跳转（去压缩子图），不计 loop
        if node_info.max_context_length is not None:
            redirect = _pre_node_context_redirect(node_info, state)
            if redirect:
                print(
                    f"\n--- [Node: {node_info.name}] Context exceeded {node_info.max_context_length}, "
                    f"redirecting to subgraph '{redirect}' (loop not counted) ---"
                )
                pre_deliv = dict(deliverables)
                if subgraph_names and redirect in subgraph_names:
                    matching = next((t for t in node_info.transitions if t.next == redirect), None)
                    pre_deliv["_replace_messages"] = bool(matching and matching.replace_messages)
                return {
                    "next_state": redirect,
                    "deliverables": pre_deliv,
                    "loop_count": state.get("loop_count", 0),  # 不计 loop
                    "max_loops": state.get("max_loops", 10),
                    "current_node": node_info.name,
                    "spans": [{
                        "node": node_info.name,
                        "loop": state.get("loop_count", 0),
                        "type": "pre_node_redirect",
                        "start": msg_start_idx,
                        "end": msg_start_idx,
                        "target": redirect,
                    }],
                }

        current_loops = state.get("loop_count", 0) + 1
        current_max_loops = state.get("max_loops", 10)
        new_max_loops = max(current_max_loops, 20) if node_info.interactive else current_max_loops

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

        # NodeStart 钩子：on: 条件求值 → 命中则抛 signal，跳过本次执行
        node_start_signal = _first_fired_signal(node_info.node_start, _condition_scope(node_info, state))
        if node_start_signal:
            target = _resolve_signal_target(node_info, node_start_signal)
            if target:
                print(
                    f"  [NodeStart] condition '{node_start_signal}' fired -> skipping execution, routing to '{target}'"
                )
                return {
                    "next_state": target,
                    "deliverables": deliverables,
                    "loop_count": current_loops,
                    "max_loops": new_max_loops,
                    "current_node": node_info.name,
                    "spans": [{
                        "node": node_info.name,
                        "loop": current_loops,
                        "type": "node_start_signal",
                        "signal": node_start_signal,
                        "start": msg_start_idx,
                        "end": msg_start_idx,
                    }],
                }
            else:
                print(
                    f"  [Warning] NodeStart signal '{node_start_signal}' has no matching condition in ## [Transitions]; ignoring."
                )

        ctx = ExecutorContext(
            node_info=node_info,
            state=state,
            tools=tools,
            safe_input=safe_input,
            run_skill=run_skill,
            settings=settings or Settings.load(),
            model=(settings.model if settings else DEFAULT_MODEL),
            base_url=(settings.base_url if settings else DEFAULT_BASE_URL),
            temperature=(settings.temperature if settings else DEFAULT_TEMPERATURE),
            config={"triggers": triggers or []},
        )
        result = executor(ctx)
        next_state = result.next_state
        output_messages = result.output_messages
        if result.payload is not None:
            deliverables["payload"] = result.payload

        # fan-out：next_state 为列表（并行多目标），跳过单目标专用逻辑（JSON 校验/审批门）
        is_fan_out = isinstance(next_state, list)

        # JSON Schema 校验与人工审批门 (通用逻辑)
        if next_state and next_state != node_info.name and not node_info.is_final and not is_fan_out:
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

        # post_node 检查点：触发 loop_count 等边界条件
        if triggers:
            _run_node_checkpoint(
                triggers,
                node_info.name,
                {
                    "loop_count": current_loops,
                    "current_node": node_info.name,
                    "next_state": next_state,
                    "deliverables": deliverables,
                    "messages": state.get("messages", []),
                    "error_flag": state.get("error_flag", False),
                    "transition_to": None,  # post_node 阶段不允许跳转（已定 next_state）
                },
            )

        # NodeEnd 钩子：on: 条件求值 → 命中则覆盖 next_state（signal 对应 Transitions 目标）
        node_end_signal = _first_fired_signal(node_info.node_end, _condition_scope(node_info, state))
        if node_end_signal:
            target = _resolve_signal_target(node_info, node_end_signal)
            if target:
                print(f"  [NodeEnd] condition '{node_end_signal}' fired -> overriding route to '{target}'")
                next_state = target
            else:
                print(
                    f"  [Warning] NodeEnd signal '{node_end_signal}' has no matching condition in ## [Transitions]; ignoring."
                )

        ret: Dict[str, Any] = {
            "next_state": next_state,
            "deliverables": deliverables,
            "loop_count": current_loops,
            "max_loops": new_max_loops,
            "current_node": node_info.name,
        }
        # 跳转子图时，标记 replace_messages（==> X <== 覆盖语义由父图后处理节点执行）
        targets: List[Any] = next_state if isinstance(next_state, list) else [next_state]
        for target in targets:
            if target and subgraph_names and target in subgraph_names:
                matching_trans = next((t for t in node_info.transitions if t.next == target), None)
                if matching_trans and matching_trans.replace_messages:
                    deliverables["_replace_messages"] = True
        # 消息归属：本节点产出的消息打 metadata（node/loop 标记，随消息持久保留）
        if output_messages is not None:
            for m in output_messages:
                meta = dict(getattr(m, "metadata", None) or {})
                meta.setdefault("node", node_info.name)
                meta.setdefault("loop", current_loops)
                setattr(m, "metadata", meta)  # BaseMessage.metadata 为实例属性
            ret["messages"] = output_messages
        # 跨度追踪：本节点调用的消息区间（start/end 索引，fan-in 时多分支 span 全部保留）
        span: Dict[str, Any] = {
            "node": node_info.name,
            "loop": current_loops,
            "type": node_info.node_type,
            "start": msg_start_idx,
            "end": msg_start_idx + (len(output_messages) if output_messages else 0),
        }
        if result.prompt_info:
            span["prompt"] = result.prompt_info
        ret["spans"] = [span]
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


def _condition_scope(node_info: NodeInfo, state: AgentState) -> Dict[str, Any]:
    """on: 条件求值的作用域变量。"""
    msgs = state.get("messages", [])
    return {
        "context_length": sum(len(getattr(m, "content", "") or "") for m in msgs),
        "loop_count": state.get("loop_count", 0),
        "current_node": state.get("current_node", ""),
        "next_state": state.get("next_state", ""),
        "deliverables": state.get("deliverables", {}),
        "messages": msgs,
        "error_flag": state.get("error_flag", False),
        "max_loops": state.get("max_loops", 10),
    }


def _eval_predicate(cond: OnCondition, scope: Dict[str, Any]) -> bool:
    """内置谓词求值。"""
    name, arg = cond.kind, cond.arg
    if name == "context_length_exceeded":
        return scope.get("context_length", 0) > (arg or 0)
    if name == "loop_count_exceeded":
        return scope.get("loop_count", 0) > (arg or 0)
    if name == "error_flag":
        return bool(scope.get("error_flag", False))
    if name == "tool_failed":
        from langchain_core.messages import ToolMessage

        return any(
            isinstance(m, ToolMessage) and getattr(m, "content", "").startswith("Error:")
            for m in scope.get("messages", [])
        )
    return False


def _eval_on_condition(cond: OnCondition, scope: Dict[str, Any]) -> bool:
    """求值一个 on: 条件（含 pyfunction / trigger 外部引用）。"""
    if cond.kind == "pyfunction":
        from langgraph_skills.triggers import _eval_pyfunction

        try:
            return _eval_pyfunction(cond.arg or "", scope)
        except Exception:
            return False
    if cond.kind == "trigger":
        # trigger: 引用外部 trigger.json —— 复用 trigger 加载与求值
        from langgraph_skills.triggers import load_triggers_from_config

        try:
            config: Dict[str, Any] = {}
            if os.path.exists(cond.arg or ""):
                with open(cond.arg, "r", encoding="utf-8") as f:
                    config = json.load(f)
            loaded = load_triggers_from_config(config)
            return any(evaluate_condition(t, scope) for t in loaded)
        except Exception:
            return False
    # 内置谓词：kind 即谓词名（context_length_exceeded / loop_count_exceeded / error_flag / tool_failed）
    return _eval_predicate(cond, scope)


def _first_fired_signal(node_hook: Optional[NodeHook], scope: Dict[str, Any]) -> Optional[str]:
    """求值钩子区块的 on: 条件列表，返回第一个命中的 signal；无命中返回 None。"""
    if not node_hook or not node_hook.conditions:
        return None
    for cond in node_hook.conditions:
        if _eval_on_condition(cond, scope):
            return cond.signal
    return None


def _resolve_signal_target(node_info: NodeInfo, signal: str) -> Optional[str]:
    """signal -> Transitions 表格 Condition 列匹配，返回目标节点名；无匹配返回 None。"""
    for t in node_info.transitions:
        if t.condition and t.condition.strip().lower() == signal.strip().lower():
            return t.next
    return None


def _pre_node_context_redirect(node_info: NodeInfo, state: AgentState) -> Optional[str]:
    """pre_node 检查点：上下文超限时，找继承跳转（==> 或 ==> <==）指向子图的 transition 并返回其目标。

    仅当节点声明 max_context_length 且当前上下文超限时触发。
    返回子图节点名（提前 return 用），无合适目标返回 None。
    """
    msgs = state.get("messages", [])
    context_length = sum(len(getattr(m, "content", "") or "") for m in msgs)
    if node_info.max_context_length is None or context_length <= node_info.max_context_length:
        return None

    # 找第一个继承跳转的 transition（==> / ==> <==，inherit_history=True）
    for t in node_info.transitions:
        if t.inherit_history:
            return t.next
    return None


def _run_node_checkpoint(
    triggers: List[Trigger],
    node_name: str,
    scope: Dict[str, Any],
) -> None:
    """post_node 检查点：求值并触发本节点的触发器。

    trigger 作用域：全局 trigger（checkpoint=post_node）在当前节点执行后触发。
    """
    for trigger in triggers_for_checkpoint(triggers, CHECKPOINT_POST_NODE):
        if not trigger.on_trigger:
            continue
        try:
            if evaluate_condition(trigger, scope):
                print(f"  [Trigger] Node '{node_name}': condition '{trigger.condition}' fired.")
                run_handler(trigger.on_trigger, scope)
        except Exception as e:
            print(f"  [Trigger] Node '{node_name}': trigger error: {e}")
