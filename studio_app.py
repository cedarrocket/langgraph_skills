import os
from langgraph_skills.graph import build_graph

# 默认加载 assistant_compiled.md 编程助手，也可以通过环境变量定制
SKILL_PATH = os.environ.get("STUDIO_SKILL_PATH", "assistant_compiled.md")

# 编译并导出 graph 变量给 LangGraph Studio 使用
graph = build_graph(SKILL_PATH)
