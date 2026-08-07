"""图构建层：IR -> LangGraph 图。

对应 PROCESS.md 设计基线的"后端降低"层：
  - build_graph：解析 skill -> 校验 -> 组装节点/边 -> 编译 LangGraph 图
  - print_help：生成 CLI 帮助菜单（供 runner 使用）

依赖方向：graph -> parser / nodes / models / tools；不依赖 runner。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from langgraph_skills.models import AgentState
from langgraph_skills.nodes import RunSkillFn, SafeInputFn, create_node, generic_router, tool_router
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph
from langgraph_skills.tools import ToolRegistry, build_tool


def print_help(skill_path: str, input_options: list) -> None:
    print(f"Usage: lgskills run {os.path.basename(skill_path)} [options] [user_input]", file=sys.stderr)
    if input_options:
        print("\nOptions:", file=sys.stderr)
        for opt in input_options:
            req = " (required)" if opt.required else ""
            default = f" (default: {opt.default})" if opt.default is not None else ""
            env = f" (env: {opt.env})" if opt.env is not None else ""
            print(f"  --{opt.name:<15} {opt.help}{req}{default}{env}", file=sys.stderr)


def build_graph(
    skill_path: str,
    initial_deliverables: Optional[Dict[str, Any]] = None,
    safe_input: Optional[SafeInputFn] = None,
    run_skill: Optional[RunSkillFn] = None,
):
    """编译 Markdown Skill 并返回标准的 LangGraph CompiledStateGraph 实例。

    每次构建使用独立的 ToolRegistry（每图隔离），不污染全局。
    safe_input / run_skill 透传给节点工厂（由 runner 注入，用于交互/嵌套 skill）。
    """
    compiled = parse_compiled_skill(skill_path)
    node_dict = compiled.nodes

    validation_errors = validate_node_graph(node_dict)
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
    for name, info in node_dict.items():
        workflow.add_node(name, create_node(info, tools, compiled.global_text, safe_input, run_skill))

    # 集中注册当前图的所有 Tools (合并成一个大 ToolNode)
    tools_node = ToolNode(tools.all())
    workflow.add_node("tools", tools_node)

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
