from langchain_core.tools import StructuredTool, tool

from langgraph_skills.models import ToolInfo
from langgraph_skills.tools import (
    TOOL_FACTORIES,
    ToolRegistry,
    build_tool,
    register_tool_factory,
)


def test_registry_builtin_aliases():
    reg = ToolRegistry()
    reg.register_builtin_aliases()
    names = {t.name for t in reg.all()}
    assert {"web_search", "read_file", "write_file"}.issubset(names)
    # 别名应指向同一对象
    assert reg.get("txt_reader") is reg.get("read_file")
    assert reg.get("txt_writer") is reg.get("write_file")


def test_registry_isolation():
    """每图隔离：两个独立注册表互不污染。"""
    reg1 = ToolRegistry()
    reg2 = ToolRegistry()

    @tool
    def only_in_one(query: str) -> str:
        """Only in one registry."""
        return query

    reg1.register("only_in_one", only_in_one)
    assert reg1.get("only_in_one") is not None
    assert reg2.get("only_in_one") is None


def test_build_tool_script(tmp_path):
    script = tmp_path / "echo_tool.py"
    script.write_text("import sys\nprint('echo:', sys.stdin.read())\n", encoding="utf-8")
    info = ToolInfo(name="echo", type="script", src=str(script), description="echo tool")
    tool_obj = build_tool(info)
    assert isinstance(tool_obj, StructuredTool)
    result = tool_obj.invoke({"payload": "hello"})
    assert "echo: hello" in result


def test_build_tool_api(monkeypatch):
    calls = {}

    class FakeResp:
        text = "api-result"

    def fake_get(url, params=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        return FakeResp()

    monkeypatch.setattr("langgraph_skills.tools.requests.get", fake_get)
    info = ToolInfo(name="api_tool", type="api", url="https://api.example.com/x", method="GET")
    tool_obj = build_tool(info)
    assert isinstance(tool_obj, StructuredTool)
    result = tool_obj.invoke({"payload": "q"})
    assert result == "api-result"
    assert calls["url"] == "https://api.example.com/x"


def test_build_tool_unknown_type_returns_none():
    info = ToolInfo(name="weird", type="unknown_type")
    assert build_tool(info) is None


def test_register_tool_factory_extension():
    @tool
    def fake_skill_tool(payload: str = "") -> str:
        """A skill-backed tool."""
        return f"skill:{payload}"

    def factory(info: ToolInfo):
        return fake_skill_tool

    register_tool_factory("skill_backed", factory)
    assert "skill_backed" in TOOL_FACTORIES
    info = ToolInfo(name="s", type="skill_backed")
    assert build_tool(info) is fake_skill_tool
