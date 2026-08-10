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

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from langgraph_skills.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    Settings,
    get_deepseek_key,
)
from langgraph_skills.models import NODE_CODE, NODE_LLM, NODE_SCRIPT, NODE_SKILL, AgentState, NodeHook, NodeInfo
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
    on_token: Optional[Callable[[str], None]] = None  # 流式回调：每个 LLM token 增量文本


@dataclass
class ExecutorResult:
    """执行器输出；node 层在此基础上做通用后处理（JSON 校验/审批门）。"""

    next_state: Optional[Any] = None  # str 或 str 列表（fan-out 多目标）
    payload: Optional[str] = None
    output_messages: Optional[List[BaseMessage]] = None
    prompt_info: Optional[Dict[str, Any]] = None  # LLM 节点的 prompt 边界信息（长度/消息构成）


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

    local_outputs: Dict[str, Optional[Any]] = {"next_state": None, "payload": None}

    def transition_to(next_state_name: Any, payload_data: str) -> None:
        local_outputs["next_state"] = next_state_name
        local_outputs["payload"] = payload_data

    local_vars = {
        "deliverables": ctx.state["deliverables"],
        "messages": ctx.state["messages"],
        "spans": ctx.state.get("spans", []),
        "get_payload": lambda: ctx.state["deliverables"].get("payload"),
        "transition_to": transition_to,
        "safe_input": ctx.safe_input,
        "HumanMessage": HumanMessage,
        "AIMessage": AIMessage,
        "SystemMessage": SystemMessage,
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

    local_outputs: Dict[str, Optional[Any]] = {"next_state": None, "payload": None}

    def transition_to(next_state_name: Any, payload_data: str) -> None:
        local_outputs["next_state"] = next_state_name
        local_outputs["payload"] = payload_data

    local_vars = {
        "deliverables": ctx.state["deliverables"],
        "messages": ctx.state["messages"],
        "spans": ctx.state.get("spans", []),
        "get_payload": lambda: ctx.state["deliverables"].get("payload"),
        "transition_to": transition_to,
        "safe_input": ctx.safe_input,
        "HumanMessage": HumanMessage,
        "AIMessage": AIMessage,
        "SystemMessage": SystemMessage,
    }

    try:
        exec(code_str.strip(), {}, local_vars)
    except Exception as e:
        print(f"Error executing script: {e}")
        raise e

    return ExecutorResult(next_state=local_outputs["next_state"], payload=local_outputs["payload"])


def execute_skill(ctx: ExecutorContext) -> ExecutorResult:
    """type=skill：运行嵌套子 skill / 子图，payload 与消息作为输入。

    消息进出：
      - 子图可见父图 messages（若调用边为 ==> / ==> <==）
      - 子图返回 messages：replace_messages=True 时整体覆盖父图 messages；
        False 时合并回父图（追加子图新增的消息）
    """
    info = ctx.node_info
    child_skill_path = info.src
    if not child_skill_path:
        raise ValueError(f"Skill state '{info.name}' is missing the 'src' attribute.")
    if not os.path.exists(child_skill_path):
        raise FileNotFoundError(f"Child skill file '{child_skill_path}' not found for state '{info.name}'.")

    parent_payload = ctx.state["deliverables"].get("payload", "")
    transition = info.transitions[0] if info.transitions else None
    inherit_history = bool(getattr(transition, "inherit_history", False))
    replace_messages = bool(getattr(transition, "replace_messages", False))

    # 子图可见父图 messages（==> / ==> <== 时继承）
    child_messages = ctx.state["messages"] if inherit_history else None
    child_deliverables = ctx.run_skill(
        child_skill_path,
        user_input=f"Parent payload context: {parent_payload}",
        initial_deliverables={"payload": parent_payload},
        initial_messages=child_messages,
        on_token=ctx.on_token,
    )

    # 消息回传：覆盖（replace）或合并（append 子图新增）
    if inherit_history and "messages" in child_deliverables:
        child_msgs = child_deliverables["messages"]
        if replace_messages:
            ctx.state["messages"] = child_msgs  # 整体覆盖
        else:
            parent_ids = {getattr(m, "id", None) for m in ctx.state["messages"]}
            new_msgs = [m for m in child_msgs if getattr(m, "id", None) not in parent_ids]
            ctx.state["messages"].extend(new_msgs)  # 合并子图新增

    next_state = transition.next if transition else None
    ctx.state["deliverables"]["_inherit_history"] = inherit_history
    ctx.state["deliverables"]["_replace_messages"] = replace_messages
    return ExecutorResult(next_state=next_state, payload=child_deliverables.get("payload", ""))


def _exec_hook_executor(
    hook: NodeHook,
    state: AgentState,
    ctx: ExecutorContext,
    *,
    signal_cb: Optional[Callable[[str], None]] = None,
    transition_cb: Optional[Callable[..., None]] = None,
    allowed_signals: Optional[set] = None,
) -> Dict[str, Any]:
    """执行一个 NodeHook 的 executor（NodeStart/NodeEnd 共用）。

    - 注入环境：state/messages/deliverables/spans/get_payload/transition_to/signal
    - 产出：ctx_messages（喂给 LLM 的消息列表）或 signal("name") 抛 condition
    - signal_cb：signal() 被调用时的回调（NodeEnd 用它记录抛出的 signal）
    - allowed_signals：允许抛出的 signal 名集合（Transitions 表格 Condition 列的值）；
      传入后 signal() 只接受集合内的名字，否则抛 ValueError（防抛未定义 signal）
    - 返回 {"ctx_messages": [...], "signal": Optional[str]}
    """
    import os as _os

    code = hook.executor
    if not code and hook.src:
        src_path = hook.src if _os.path.isabs(hook.src) else hook.src
        if not _os.path.exists(src_path):
            raise FileNotFoundError(f"Hook executor src not found: {src_path}")
        with open(src_path, "r", encoding="utf-8") as f:
            code = f.read()
    if not code:
        return {"ctx_messages": None, "signal": None}

    code = _strip_code_fence(code)

    emitted: Dict[str, Any] = {"signal": None}

    def _signal(name: str) -> None:
        if allowed_signals is not None and name not in allowed_signals:
            raise ValueError(
                f"signal({name!r}) is not defined in ## [Transitions] Condition column. "
                f"Available signals: {sorted(allowed_signals)}"
            )
        emitted["signal"] = name
        if signal_cb is not None:
            signal_cb(name)

    local_vars: Dict[str, Any] = {
        "state": state,
        "messages": state.get("messages", []),
        "deliverables": state.get("deliverables", {}),
        "spans": state.get("spans", []),
        "get_payload": lambda: state.get("deliverables", {}).get("payload"),
        "HumanMessage": HumanMessage,
        "AIMessage": AIMessage,
        "SystemMessage": SystemMessage,
        "transition_to": transition_cb or (lambda *a, **k: None),
        "signal": _signal,
        "ctx_messages": None,
    }

    try:
        exec(code, {}, local_vars)
    except Exception as e:
        print(f"Error executing hook executor: {e}")
        raise e

    result = local_vars.get("ctx_messages")
    return {"ctx_messages": list(result) if result else None, "signal": emitted["signal"]}


def _run_context_executor(node_start: NodeHook, state: AgentState, ctx: ExecutorContext) -> List[BaseMessage]:
    """执行 NodeStart 的 executor，产出 ctx_messages（喂给 LLM 的消息列表）。"""
    result = _exec_hook_executor(node_start, state, ctx)
    return list(result["ctx_messages"]) if result["ctx_messages"] else []


def _invoke_llm(llm_with_tools: Any, messages: List[BaseMessage], ctx: ExecutorContext) -> Any:
    """调用 LLM。有 on_token 回调时走 stream（逐 token 回调 + 聚合完整响应），否则 invoke。

    返回完整 response（AIMessage），包含 tool_calls / metadata / content。
    """
    if ctx.on_token is None:
        return llm_with_tools.invoke(messages)

    # stream 模式：逐 chunk 回调 token 文本，同时累积聚合

    chunks: List[Any] = []
    for chunk in llm_with_tools.stream(messages):
        chunks.append(chunk)
        if getattr(chunk, "content", None):
            ctx.on_token(str(chunk.content))

    if not chunks:
        return llm_with_tools.invoke(messages)

    merged = chunks[0]
    for c in chunks[1:]:
        merged += c  # type: ignore[operator]
    # 聚合后的 AIMessageChunk 用 + 已是 AIMessage（保留 tool_calls/metadata）；保险起见转 message
    to_message = getattr(merged, "to_message", None)
    if callable(to_message):
        merged = to_message()
    # 清理 tool_calls：stream 聚合时 name 可能被重复拼接（LangChain chunk 加法对 name 做字符串拼接），
    # 按 id 分组取非空 name 修正
    if merged.tool_calls:
        name_by_id: Dict[str, str] = {}
        for chunk in chunks:
            for tcc in getattr(chunk, "tool_call_chunks", []) or []:
                n = tcc.get("name") or ""
                if n and tcc.get("id"):
                    name_by_id[tcc["id"]] = n
        if name_by_id:
            for tc in merged.tool_calls:
                fixed = name_by_id.get(tc.get("id", ""))
                if fixed:
                    tc["name"] = fixed
    return merged


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
        params = {k: v for k, v in state["deliverables"].items() if k not in ("feedback", "start_msg_index", "exit_code", "payload", "_inherit_history")}
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

    # 历史窗口 / 上下文模式（NodeStart context 决定本节点可见消息）
    current_node_messages: List[BaseMessage] = []
    node_start = info.node_start
    if node_start is not None and node_start.context == "previous_payload":
        # previous_payload：只继承上一节点最终 payload
        prev_payload = state["deliverables"].get("payload", "")
        current_node_messages = (
            [HumanMessage(content=f"[Context from previous stage]:\n{prev_payload}")] if prev_payload else []
        )
    elif node_start is not None and node_start.context == "executor":
        # executor：执行 NodeStart 代码块，产出 ctx_messages（喂给 LLM 的消息列表）
        current_node_messages = _run_context_executor(node_start, state, ctx)
        if not current_node_messages:
            raise RuntimeError(
                f"Node '{info.name}': context: executor produced no ctx_messages. "
                "The executor must set ctx_messages to a non-empty list."
            )
    else:
        # all（缺省）：沿用现有继承游标逻辑
        if "start_msg_index" not in state["deliverables"]:
            state["deliverables"]["start_msg_index"] = 0
            current_node_messages = state["messages"]
        elif info.name != state["current_node"]:
            if state["deliverables"].get("_inherit_history", False):
                # ==> 跳转：继承源节点的消息历史（游标不重置，从源节点起点继续看）
                start_idx = state["deliverables"].get("start_msg_index", 0)
                current_node_messages = state["messages"][start_idx:]
                state["deliverables"]["_inherit_history"] = False  # 一次性继承，用完清除
            else:
                # -> 跳转（默认）：重置游标，本节点从空开始（现状零继承）
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

    # pre_llm 检查点：触发 context_length 等边界条件（LLM 调用前）
    llm_triggers = ctx.config.get("triggers", []) if isinstance(ctx.config, dict) else []
    if llm_triggers:
        _run_pre_llm_checkpoint(llm_triggers, info, state, messages)

    response = _invoke_llm(llm_with_tools, messages, ctx)

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
                deliverables["_inherit_history"] = bool(matching_trans and matching_trans.inherit_history)
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
            deliverables["_inherit_history"] = info.transitions[0].inherit_history
            print(f"  -> Auto-transition to: {next_state} (unconditional)")
            triggered_transition = True

    if info.interactive and not triggered_transition:
        print(f"\nAI: {response.content}\n")
        user_input = ctx.safe_input("You: ")
        out_msgs.append(HumanMessage(content=user_input))
        next_state = info.name

    return ExecutorResult(
        next_state=next_state,
        payload=payload,
        output_messages=out_msgs,
        prompt_info={
            "prompt_messages": len(messages),  # 本节点 prompt 的消息条数
            "prompt_chars": sum(len(getattr(m, "content", "") or "") for m in messages),
            "input_start": len(state.get("messages", [])),  # 本次调用输入的消息起点（索引）
            "input_msgs": len(state.get("messages", [])),  # 本次调用输入的消息条数
        },
    )


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


def _run_pre_llm_checkpoint(
    triggers: List[Any],
    info: NodeInfo,
    state: AgentState,
    messages: List[BaseMessage],
) -> None:
    """pre_llm 检查点：LLM 调用前求值 context_length 等边界条件并触发 handler。"""
    from langgraph_skills.triggers import (
        CHECKPOINT_PRE_LLM,
        evaluate_condition,
        run_handler,
        triggers_for_checkpoint,
    )

    scope = {
        "context_length": sum(len(getattr(m, "content", "") or "") for m in messages),
        "loop_count": state.get("loop_count", 0),
        "current_node": state.get("current_node", ""),
        "next_state": state.get("next_state", ""),
        "deliverables": state.get("deliverables", {}),
        "messages": state.get("messages", []),
        "error_flag": state.get("error_flag", False),
    }
    for trigger in triggers_for_checkpoint(triggers, CHECKPOINT_PRE_LLM):
        if not trigger.on_trigger:
            continue
        try:
            if evaluate_condition(trigger, scope):
                print(f"  [Trigger] Node '{info.name}': condition '{trigger.condition}' fired (pre_llm).")
                run_handler(trigger.on_trigger, scope)
        except Exception as e:
            print(f"  [Trigger] Node '{info.name}': trigger error: {e}")
