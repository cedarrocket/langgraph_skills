"""图构建层：IR -> LangGraph 图。

对应 PROCESS.md 设计基线的"后端降低"层：
  - build_graph：解析 skill -> 校验 -> 组装节点/边 -> 编译 LangGraph 图
  - print_help：生成 CLI 帮助菜单（供 runner 使用）

依赖方向：graph -> parser / nodes / models / tools；不依赖 runner。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from langgraph_skills.config import Settings
from langgraph_skills.debug import dprint
from langgraph_skills.hooks import ToolHooks, load_hooks
from langgraph_skills.models import AgentState, ReplaceMessages, SubGraphInfo
from langgraph_skills.nodes import RunSkillFn, SafeInputFn, create_node, generic_router, tool_router
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph
from langgraph_skills.tools import ToolRegistry, build_tool
from langgraph_skills.triggers import Trigger


def print_help(skill_path: str, input_options: list) -> None:
    print(f"Usage: lgskills run {os.path.basename(skill_path)} [options] [user_input]", file=sys.stderr)
    if input_options:
        print("\nOptions:", file=sys.stderr)
        for opt in input_options:
            req = " (required)" if opt.required else ""
            default = f" (default: {opt.default})" if opt.default is not None else ""
            env = f" (env: {opt.env})" if opt.env is not None else ""
            print(f"  --{opt.name:<15} {opt.help}{req}{default}{env}", file=sys.stderr)


def _make_subgraph_after(sub_name: str):
    """子图后处理节点：执行 ==> X <== 覆盖语义。

    子图返回后，若 deliverables 带 _replace_messages 与 _child_messages
    （子图内部作者在压缩节点设置），则整体替换父图 messages，并清除标志。
    返回 {} 或替换后的 state 更新；控制流继续由 generic_router 决定。
    """

    def after(state: AgentState) -> Dict[str, Any]:
        deliv = state.get("deliverables", {})
        ret: Dict[str, Any] = {}
        if deliv.get("_replace_messages"):
            child_msgs = deliv.get("_child_messages")
            deliv = {k: v for k, v in deliv.items() if k not in ("_replace_messages", "_child_messages")}
            ret["deliverables"] = deliv
            if child_msgs:
                ret["messages"] = ReplaceMessages(list(child_msgs))
                dprint(f"--- [SubGraph: {sub_name}] replace_messages: 父图 messages 已被子图输出替换 ({len(child_msgs)} 条) ---")
        return ret

    return after


def _compile_subgraph(
    sub: SubGraphInfo,
    base_dir: str,
    tools: ToolRegistry,
    global_text: str,
    safe_input: Optional[SafeInputFn],
    run_skill: Optional[RunSkillFn],
    settings: Optional[Settings],
    triggers: Optional[List[Trigger]],
    subgraph_names: Optional[set] = None,
    tool_hooks: Optional[ToolHooks] = None,
    on_token: Optional[Callable[[str], None]] = None,
):
    """把 # [SubGraph] 编译为 LangGraph 子图（真子图）。

    - 形态 A（sub.nodes）：内部节点用 create_node + generic_router（复用现有机制）
    - src 简写（sub.src）：递归 build_graph 外部 skill 文件
    """
    if sub.src:
        # src 简写：外部文件路径（相对子图声明所在目录）
        src_path = sub.src if os.path.isabs(sub.src) else os.path.join(base_dir, sub.src)
        return build_graph(
            src_path,
            None,
            safe_input=safe_input,
            run_skill=run_skill,
            settings=settings,
            triggers=triggers,
            is_subgraph=True,
        )

    # 形态 A：内部节点
    sub_dict = sub.nodes
    if not sub_dict:
        raise ValueError(f"SubGraph '{sub.name}' has no nodes and no src.")

    # 校验子图内部节点（transition_to 声明检查、悬空目标等）
    sub_subgraph_names = {n for n, i in sub_dict.items() if i.node_type == "subgraph" and i.subgraph}
    sub_errors = validate_node_graph(
        sub_dict,
        subgraph_names=sub_subgraph_names,
        allow_last_implicit_end=True,
        strict_transitions=True,
    )
    if sub_errors:
        raise ValueError(f"SubGraph '{sub.name}':\n" + "\n".join(sub_errors))

    workflow = StateGraph(AgentState)
    for name, info in sub_dict.items():
        if info.node_type == "subgraph" and info.subgraph:
            # 嵌套子图：递归编译为真子图节点 + 后处理节点
            nested = info.subgraph
            nested_graph = _compile_subgraph(
                nested, base_dir, tools, global_text, safe_input, run_skill, settings, triggers, subgraph_names, tool_hooks, on_token
            )
            workflow.add_node(name, nested_graph)
            workflow.add_node(f"_sub_after_{name}", _make_subgraph_after(name))
        else:
            workflow.add_node(name, create_node(info, tools, global_text, safe_input, run_skill, settings, triggers, subgraph_names, on_token))

    tools_node = ToolNode(tools.all())
    workflow.add_node("tools", _make_tool_node(tools_node, tool_hooks))

    for name, info in sub_dict.items():
        if info.node_type == "subgraph" and info.subgraph:
            # 嵌套子图节点：执行完 → 后处理节点（覆盖语义）→ 条件路由
            workflow.add_edge(name, f"_sub_after_{name}")
            workflow.add_conditional_edges(
                f"_sub_after_{name}",
                generic_router,
                path_map={
                    "tools": "tools",
                    **{s: s for s in sub_dict.keys()},
                    END: END,
                },
            )
        elif info.is_final:
            workflow.add_edge(name, END)
        else:
            workflow.add_conditional_edges(
                name,
                generic_router,
                path_map={
                    "tools": "tools",
                    **{s: s for s in sub_dict.keys()},
                    END: END,
                },
            )
    workflow.add_conditional_edges(
        "tools",
        tool_router,
        path_map={s: s for s in sub_dict.keys()},
    )

    start_node = list(sub_dict.keys())[0]
    workflow.set_entry_point(start_node)
    return workflow.compile()


def _make_tool_node(tools_node: Any, hooks: Optional[ToolHooks] = None):
    """包装 ToolNode：工具调用消息打 metadata + 记录 tool span + 工具边界 hooks。

    - ToolMessage 打 metadata：node=tools / tool=<工具名> / args=<调用参数>
    - 返回 spans：记录本次工具调用的 {tool, args, result, start, end}
    - 工具边界 hooks：pre_tool（调用前）/ post_tool（调用后）/ post_tool_failure
    分支入口的 python 脚本可按 metadata + spans 区分每一步（含工具调用）。
    """
    from langgraph_skills.hooks import (
        CHECKPOINT_POST_TOOL,
        CHECKPOINT_POST_TOOL_FAILURE,
        CHECKPOINT_PRE_TOOL,
        fire_tool_hooks,
    )

    def wrapped_tool_node(state: AgentState) -> Dict[str, Any]:
        msg_start_idx = len(state.get("messages", []))
        # 找触发本次工具调用的 AIMessage（最后带 tool_calls 的那条）
        trigger_ai = None
        for m in reversed(state.get("messages", [])):
            if isinstance(m, AIMessage) and m.tool_calls:
                trigger_ai = m
                break
        calls_by_id = {c["id"]: c for c in (trigger_ai.tool_calls if trigger_ai else [])}

        tool_names = list(calls_by_id.values())

        # pre_tool hook（调用前）：每个待调工具
        if hooks:
            for call in tool_names:
                name = call.get("name", "")
                if not name:
                    continue
                hook_scope = {
                    "deliverables": state.get("deliverables", {}),
                    "messages": state.get("messages", []),
                    "tool_name": name,
                    "tool_args": call.get("args", {}),
                    "current_node": state.get("current_node", ""),
                }
                fire_tool_hooks(hooks, CHECKPOINT_PRE_TOOL, hook_scope, name)

        out = tools_node.invoke(state)

        spans: List[Dict[str, Any]] = []
        out_msgs = out.get("messages", [])
        for i, m in enumerate(out_msgs):
            tool_name = getattr(m, "name", None) or ""
            meta = dict(getattr(m, "metadata", None) or {})
            meta.setdefault("node", "tools")
            meta.setdefault("tool", tool_name)
            matched_call = calls_by_id.get(getattr(m, "tool_call_id", None))
            call_args = matched_call.get("args") if matched_call else None
            if call_args is not None:
                meta["args"] = call_args
            setattr(m, "metadata", meta)
            is_failure = isinstance(getattr(m, "content", ""), str) and m.content.startswith("Error:")
            spans.append({
                "node": "tools",
                "loop": 0,
                "type": "tool",
                "tool": tool_name,
                "args": call_args,
                "result": m.content,
                "start": msg_start_idx + i,
                "end": msg_start_idx + i + 1,
            })
            # post_tool / post_tool_failure hook（调用后）
            if hooks and tool_name:
                checkpoint = CHECKPOINT_POST_TOOL_FAILURE if is_failure else CHECKPOINT_POST_TOOL
                hook_scope = {
                    "deliverables": state.get("deliverables", {}),
                    "messages": state.get("messages", []),
                    "tool_name": tool_name,
                    "tool_args": call_args or {},
                    "tool_result": m.content,
                    "current_node": state.get("current_node", ""),
                }
                fire_tool_hooks(hooks, checkpoint, hook_scope, tool_name)
        out["spans"] = spans
        return out

    return wrapped_tool_node


def build_graph(
    skill_path: str,
    initial_deliverables: Optional[Dict[str, Any]] = None,
    safe_input: Optional[SafeInputFn] = None,
    run_skill: Optional[RunSkillFn] = None,
    settings: Optional[Settings] = None,
    triggers: Optional[List[Trigger]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    is_subgraph: bool = False,
):
    """编译 Markdown Skill 并返回标准的 LangGraph CompiledStateGraph 实例。

    每次构建使用独立的 ToolRegistry（每图隔离），不污染全局。
    safe_input / run_skill / settings / triggers 透传给节点工厂（由 runner 注入）。
    """
    compiled = parse_compiled_skill(skill_path)
    node_dict = compiled.nodes

    # 加载工具边界 hooks（hooks.json，全局 + 项目拼接）
    tool_hooks = load_hooks()

    # is_subgraph（src 简写子图独立文件）：节点可跳主图节点（Agent 等），
    # 跳转目标存在性/声明由主图上下文校验，独立文件放行
    validation_errors = validate_node_graph(
        node_dict,
        subgraph_names=set(compiled.subgraphs.keys()),
        skip_transition_target_check=is_subgraph,
    )
    if validation_errors:
        err_msg = "\n".join(validation_errors)
        raise ValueError(err_msg)

    if initial_deliverables is None:
        initial_deliverables = {}

    # 每图独立的工具注册表
    tools = ToolRegistry()
    tools.register_builtin_aliases()

    # 从 tools/ 目录动态加载自定义工具
    cwd_tools = os.path.join(os.getcwd(), "tools")
    tools.load_from_dir(cwd_tools)

    skill_dir_tools = os.path.join(os.path.dirname(os.path.abspath(skill_path)), "tools")
    if skill_dir_tools != cwd_tools:
        tools.load_from_dir(skill_dir_tools)

    # 动态构建并注册声明式工具（script / api 等，工厂可扩展）
    for t_name, t_info in compiled.tools.items():
        tool_obj = build_tool(t_info)
        if tool_obj is not None:
            tools.register(t_name, tool_obj)

    # 自动处理 Option 中的 reader 属性
    for opt in compiled.input_options:
        reader_tool_name = opt.reader
        if reader_tool_name:
            name = opt.name
            filepath = initial_deliverables.get(name)
            content = None
            if filepath and filepath != "-":
                tool_func = tools.get(reader_tool_name)
                if tool_func is not None:
                    try:
                        res = tool_func.invoke({"filepath": filepath})
                        if isinstance(res, str) and not res.startswith("Error:"):
                            content = res
                    except Exception:
                        pass
            if content is not None:
                initial_deliverables[name + "_content"] = content
                if "payload" not in initial_deliverables:
                    initial_deliverables["payload"] = content

    workflow = StateGraph(AgentState)

    # 添加所有节点
    subgraph_names = set(compiled.subgraphs.keys())
    for name, info in node_dict.items():
        workflow.add_node(name, create_node(info, tools, compiled.global_text, safe_input, run_skill, settings, triggers, subgraph_names, on_token))

    # 编译并注册子图节点（# [SubGraph]）——真子图，作为父图节点
    base_dir = os.path.dirname(os.path.abspath(skill_path))
    for sub_name, sub in compiled.subgraphs.items():
        sub_graph = _compile_subgraph(
            sub, base_dir, tools, compiled.global_text, safe_input, run_skill, settings, triggers, subgraph_names, tool_hooks, on_token
        )
        workflow.add_node(sub_name, sub_graph)
        # 子图后处理节点：==> X <== 覆盖语义（子图返回后整体替换父图 messages）
        workflow.add_node(f"_sub_after_{sub_name}", _make_subgraph_after(sub_name))

    # 集中注册当前图的所有 Tools (合并成一个大 ToolNode)
    tools_node = ToolNode(tools.all())
    workflow.add_node("tools", _make_tool_node(tools_node, tool_hooks))

    # 构建边 (Edges)
    for name, info in node_dict.items():
        if info.is_final:
            workflow.add_edge(name, END)
        else:
            workflow.add_conditional_edges(
                name,
                generic_router,
                path_map={
                    "tools": "tools",
                    **{s: s for s in node_dict.keys()},
                    **{s: s for s in compiled.subgraphs.keys()},  # 子图也是合法路由目标
                    END: END,
                },
            )

    # 子图节点边：子图执行完 → 子图后处理节点（处理覆盖语义）→ 按 generic_router 路由回父图
    for sub_name in compiled.subgraphs:
        workflow.add_edge(sub_name, f"_sub_after_{sub_name}")
        workflow.add_conditional_edges(
            f"_sub_after_{sub_name}",
            generic_router,
            path_map={
                "tools": "tools",
                **{s: s for s in node_dict.keys()},
                **{s: s for s in compiled.subgraphs.keys()},
                END: END,
            },
        )

    # 从 Tools 执行完毕后，必须返回刚才的节点继续 ReAct
    workflow.add_conditional_edges(
        "tools",
        tool_router,
        path_map={s: s for s in node_dict.keys()},
    )

    start_node = list(node_dict.keys())[0]
    workflow.set_entry_point(start_node)

    return workflow.compile()
