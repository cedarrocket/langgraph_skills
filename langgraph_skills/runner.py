"""运行时编排层：解析 -> 构建图 -> 执行 + CLI。

对应 PROCESS.md 设计基线的"运行时"层：
  - safe_input：交互式输入（终端/管道）
  - run_skill：完整执行一个 skill（解析 -> 构建 -> stream）
  - run_cli：命令行入口（参数解析、stdin、writer 落盘、退出码）

依赖方向：runner -> graph / parser / models / config / tools；不反向依赖任何上层。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import END

from langgraph_skills.config import get_deepseek_key
from langgraph_skills.graph import build_graph, print_help
from langgraph_skills.models import AgentState
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph
from langgraph_skills.tools import ToolRegistry, build_tool


def safe_input(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        if not sys.stdin.isatty():
            with open("/dev/tty", "r", encoding="utf-8") as tty:
                return tty.readline().rstrip("\r\n")
    except Exception:
        pass

    line = sys.stdin.readline()
    if not line:
        raise EOFError("EOF when reading a line")
    return line.rstrip("\r\n")


def run_skill(
    skill_path: str,
    user_input: str = "User Input: What is Quantum Computing?",
    initial_deliverables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    print(f"1. Parsing and compiling skill graph: {skill_path}...", file=sys.stderr)
    compiled = parse_compiled_skill(skill_path)
    node_dict = compiled.nodes

    validation_errors = validate_node_graph(node_dict)
    if validation_errors:
        err_msg = "\n".join(validation_errors)
        print(f"Validation Error: {err_msg}", file=sys.stderr)
        raise ValueError(err_msg)

    # 校验输入参数并应用默认值与环境变量
    if initial_deliverables is None:
        initial_deliverables = {}

    # Normalize input and output option keys
    for std_name in ["input_path", "output_path"]:
        alias = std_name.replace("_path", "")  # "input" or "output"
        if std_name not in initial_deliverables and alias in initial_deliverables:
            initial_deliverables[std_name] = initial_deliverables[alias]
        elif alias not in initial_deliverables and std_name in initial_deliverables:
            initial_deliverables[alias] = initial_deliverables[std_name]

    for opt in compiled.input_options:
        name = opt.name
        env_val = os.environ.get(opt.env) if opt.env else None

        if name not in initial_deliverables:
            if env_val is not None:
                initial_deliverables[name] = env_val
            elif opt.default is not None:
                initial_deliverables[name] = opt.default
            elif opt.required:
                print(f"Error: Missing required option '--{name}'", file=sys.stderr)
                print_help(skill_path, compiled.input_options)
                sys.exit(2)

    # 动态构建并注册工具及运行 Option 中的 reader 属性
    app = build_graph(skill_path, initial_deliverables, safe_input=safe_input, run_skill=run_skill)

    start_node = list(node_dict.keys())[0]
    initial_state: AgentState = {
        "messages": [HumanMessage(content=user_input)],
        "global_instructions": compiled.global_text,
        "state_instructions": node_dict[start_node].instructions,
        "deliverables": initial_deliverables if initial_deliverables is not None else {},
        "next_state": "",
        "current_node": start_node,
        "loop_count": 0,
        "max_loops": compiled.max_loops,
    }

    final_deliverables = initial_deliverables if initial_deliverables is not None else {}
    if get_deepseek_key():
        for output in app.stream(initial_state):
            for key, value in output.items():
                print(f"Output from node '{key}':")
                print(value)
                if isinstance(value, dict):
                    if "deliverables" in value:
                        final_deliverables = value["deliverables"]
                    if value.get("loop_count", 0) >= value.get("max_loops", 10) and value.get("next_state") == END:
                        final_deliverables["exit_code"] = 3
    else:
        print("DeepSeek API key not found. Graph compiled successfully but skipping execution.")

    return final_deliverables


def run_cli(skill_file: str, remaining_args: List[str]) -> None:
    # Parse CLI options
    initial_deliverables = {}

    user_input = "Start."
    show_help = False

    # Check if piping stdin (non-TTY)
    if not sys.stdin.isatty():
        try:
            initial_deliverables["stdin"] = sys.stdin.read()
        except Exception as e:
            print(f"Warning: Failed to read from stdin: {e}", file=sys.stderr)

    i = 0
    while i < len(remaining_args):
        arg = remaining_args[i]
        if arg in ("-h", "--help"):
            show_help = True
            i += 1
        elif arg.startswith("-"):
            # Handle option
            if arg.startswith("--"):
                key = arg[2:]
            else:
                key = arg[1:]
                # Map short options to standard aliases
                if key == "i":
                    key = "input"
                elif key == "o":
                    key = "output"

            if i + 1 < len(remaining_args) and (not remaining_args[i + 1].startswith("-") or remaining_args[i + 1] == "-"):
                val = remaining_args[i + 1]
                initial_deliverables[key] = val
                i += 2
            else:
                initial_deliverables[key] = "true"
                i += 1
        else:
            user_input = arg
            i += 1

    if show_help:
        try:
            input_options = parse_compiled_skill(skill_file).input_options
            print_help(skill_file, input_options)
        except Exception as e:
            print(f"Error parsing skill help: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc(file=sys.stderr)
        sys.exit(0)

    # Redirect stdout to stderr for clean Unix logs separation
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        final_deliverables = run_skill(skill_file, user_input, initial_deliverables)
        exit_code = int(final_deliverables.get("exit_code", 0))

        # Output only the final payload to stdout (or automatic writer file)
        payload = final_deliverables.get("payload", "")

        # 自动处理 Option 中的 writer 属性（使用每图独立的工具注册表）
        compiled = parse_compiled_skill(skill_file)
        writer_tools = ToolRegistry()
        writer_tools.register_builtin_aliases()
        for t_info in compiled.tools.values():
            tool_obj = build_tool(t_info)
            if tool_obj is not None:
                writer_tools.register(t_info.name, tool_obj)

        written_to_file = False
        for opt in compiled.input_options:
            writer_tool_name = opt.writer
            if writer_tool_name:
                name = opt.name
                filepath = final_deliverables.get(name)
                if filepath and filepath != "-":
                    tool_func = writer_tools.get(writer_tool_name)
                    if tool_func is None:
                        print(f"Error: Writer tool '{writer_tool_name}' not registered.", file=sys.stderr)
                        sys.exit(1)
                    print(
                        f"  [Auto-Writer] Writing final payload to '{filepath}' using '{writer_tool_name}'...",
                        file=sys.stderr,
                    )
                    try:
                        res = tool_func.invoke({"filepath": filepath, "content": payload})
                        if isinstance(res, str) and res.startswith("Success:"):
                            print(f"  [Auto-Writer] Saved successfully to {filepath}", file=sys.stderr)
                            written_to_file = True
                        else:
                            print(f"Error writing file via writer '{writer_tool_name}': {res}", file=sys.stderr)
                            sys.exit(1)
                    except Exception as e:
                        print(f"Error executing writer tool '{writer_tool_name}': {e}", file=sys.stderr)
                        sys.exit(1)

        if not written_to_file:
            original_stdout.write(payload + "\n")
            original_stdout.flush()

        sys.exit(exit_code)
    except SystemExit as se:
        raise se
    except Exception as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
