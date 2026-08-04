"""工具注册表（每图隔离）与工具工厂。

对应 PROCESS.md 设计基线的"工具注册表"层：
  - ToolRegistry：每次 build_graph 创建独立实例，避免全局状态污染（嵌套 skill 也各用各的）
  - 内置工具（web_search / read_file / write_file）
  - 声明式工具工厂（script / api）—— 通过 ToolFactory 可扩展新的工具种类

依赖方向：tools -> models（ToolInfo）；不依赖 interpreter / executors / parser。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
from typing import Callable, Dict, List, Optional

import requests
from langchain_core.tools import BaseTool, StructuredTool, tool

from langgraph_skills.models import TOOL_API, TOOL_SCRIPT, ToolInfo

# ---------------------------------------------------------------------------
# 内置工具
# ---------------------------------------------------------------------------


@tool
def web_search(query: str) -> str:
    """Search the web for the given query."""
    print(f"  [Tool Executing] web_search with query: '{query}'")
    if "quantum" in query.lower():
        return "Quantum computing uses qubits which can be in superposition (both 0 and 1) and entanglement."
    return f"Search results for '{query}': No specific info found, but search executed successfully."


@tool
def read_file(filepath: str) -> str:
    """Read full text contents of a file."""
    print(f"  [Tool Executing] read_file for: '{filepath}'")
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error: Failed to read file '{filepath}': {e}"
    return f"Error: File '{filepath}' not found."


@tool
def write_file(filepath: str, content: str) -> str:
    """Write text content to a file at the specified filepath."""
    print(f"  [Tool Executing] write_file to: '{filepath}'")
    try:
        dirname = os.path.dirname(os.path.abspath(filepath))
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Success: Content written to '{filepath}'"
    except Exception as e:
        return f"Error: Failed to write file '{filepath}': {e}"


# ---------------------------------------------------------------------------
# 工具注册表（每图隔离）
# ---------------------------------------------------------------------------


class ToolRegistry:
    """一次图构建内的工具集合。每次 build_graph 新建实例，互不污染。"""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, name: str, tool_obj: BaseTool) -> None:
        self._tools[name] = tool_obj

    def register_builtin_aliases(self) -> None:
        self.register("web_search", web_search)
        self.register("read_file", read_file)
        self.register("write_file", write_file)
        self.register("txt_reader", read_file)
        self.register("txt_writer", write_file)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def resolve_many(self, names: List[str]) -> List[BaseTool]:
        return [self._tools[n] for n in names if n in self._tools]

    def all(self) -> List[BaseTool]:
        return list(self._tools.values())

    def load_from_dir(self, directory: str) -> None:
        """扫描目录下不以 `_` 开头的 .py，注册其中 BaseTool 实例。"""
        if not os.path.exists(directory) or not os.path.isdir(directory):
            return
        if directory not in sys.path:
            sys.path.insert(0, directory)

        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            module_name = filename[:-3]
            filepath = os.path.join(directory, filename)
            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    for _, obj in inspect.getmembers(module):
                        if isinstance(obj, BaseTool):
                            print(
                                f"  [Tool Registry] Dynamically registered custom tool '{obj.name}' from {filename}",
                                file=sys.stderr,
                            )
                            self.register(obj.name, obj)
            except Exception as e:
                print(f"  [Warning] Failed to load custom tool file {filename}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 工具工厂（可扩展）
# ---------------------------------------------------------------------------
# ToolFactory: Dict[tool_type, (ToolInfo) -> BaseTool]
# 新的工具种类（如未来的 skill-tool）只需 register_tool_factory("skill", ...)，无需改核心。

ToolFactory = Callable[[ToolInfo], BaseTool]


def _make_script_tool(tool_info: ToolInfo) -> StructuredTool:
    desc = tool_info.description or f"Execute script tool: {tool_info.name}"

    def tool_func(payload: str = "") -> str:
        script_path = tool_info.src
        if not script_path:
            return f"Error: Script tool '{tool_info.name}' has no 'src'."
        if not os.path.exists(script_path):
            return f"Error: Script file {script_path} not found."
        print(f"  [Tool Executing] Running script tool '{tool_info.name}' with argument payload.")
        result = subprocess.run(
            [sys.executable, script_path],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return (result.stdout + "\n" + result.stderr).strip()

    tool_func.__doc__ = desc
    tool_func.__name__ = tool_info.name
    return StructuredTool.from_function(func=tool_func, name=tool_info.name, description=desc)


def _make_api_tool(tool_info: ToolInfo) -> StructuredTool:
    desc = tool_info.description or f"Call API: {tool_info.name}"

    def tool_func(payload: str = "") -> str:
        url = tool_info.url
        if not url:
            return f"Error: API tool '{tool_info.name}' has no 'url'."
        method = tool_info.method
        print(f"  [Tool Executing] Calling API '{tool_info.name}' ({method} {url})")
        try:
            if method == "POST":
                res = requests.post(url, json={"payload": payload}, timeout=10)
            else:
                res = requests.get(url, params={"payload": payload}, timeout=10)
            return res.text
        except Exception as e:
            return f"Error calling API {url}: {e}"

    tool_func.__doc__ = desc
    tool_func.__name__ = tool_info.name
    return StructuredTool.from_function(func=tool_func, name=tool_info.name, description=desc)


TOOL_FACTORIES: Dict[str, ToolFactory] = {
    TOOL_SCRIPT: _make_script_tool,
    TOOL_API: _make_api_tool,
}


def register_tool_factory(tool_type: str, factory: ToolFactory) -> None:
    """注册新的工具种类工厂（扩展点）。"""
    TOOL_FACTORIES[tool_type] = factory


def build_tool(tool_info: ToolInfo) -> Optional[BaseTool]:
    """根据 ToolInfo 构造工具；未知类型返回 None。"""
    factory = TOOL_FACTORIES.get(tool_info.type)
    if factory is None:
        print(f"  [Warning] Unknown tool type '{tool_info.type}' for '{tool_info.name}'.", file=sys.stderr)
        return None
    return factory(tool_info)
