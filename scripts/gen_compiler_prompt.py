"""从 spec/dsl_spec.yaml 生成编译器的 prompt，保证与 parser 同构。

生成两个变体：
  - python 版：langgraph_skills/compiler.py 的 COMPILER_PROMPT（含 {draft} 占位符）
  - skill 版：bootstrap_compiled.md 中 CompileDraft 节点的 instructions（草稿经 payload 传入）

用法:
    python scripts/gen_compiler_prompt.py              # 更新 compiler.py + bootstrap_compiled.md
    python scripts/gen_compiler_prompt.py --stdout     # 打印 python 版 prompt 到 stdout

设计（方案 A）:
    语法事实（section 定义、默认值、transition 语法、语义规则）从 spec 生成；
    外层人工措辞（语气、目标、输出约束）保留在本文档的模板中。
    修改 DSL 语法后：先回写 dsl_spec.yaml -> 重新运行本脚本 -> 检查 diff。
"""

import sys
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "spec" / "dsl_spec.yaml"
COMPILER_PATH = ROOT / "langgraph_skills" / "compiler.py"
BOOTSTRAP_PATH = ROOT / "bootstrap_compiled.md"

# ---------------------------------------------------------------------------
# 人工措辞模板（教学/语气部分，不来自 spec）
# ---------------------------------------------------------------------------
HEADER = """You are a LangGraph Skill Compiler.
Your task is to read a draft skill written by a human (which might have loose syntax, informal node declarations, or informal transition descriptions) and compile it into a standard Markdown AST state machine format."""

RULES_INTRO = """Rules:
"""

# python 版结尾（含 {draft} 占位符，供 compiler.py 模板使用）
FOOTER_PY = """Output ONLY the compiled Markdown containing the valid `# [Node]` and `## [Transitions]` structures. Do not wrap the output in markdown code blocks unless the input draft itself was wrapped.
If the input draft has a shebang line (e.g., #!...) or a # [Config] / # [IO] block, you MUST fully preserve them exactly as-is at the very top of the compiled output.

Draft Skill:
{draft}
"""

# skill 版结尾（草稿经 payload 传入，末尾加引导语）
FOOTER_SKILL = """Output ONLY the compiled Markdown containing the valid `# [Node]` and `## [Transitions]` structures. Do not wrap the output in markdown code blocks unless the input draft itself was wrapped.
If the input draft has a shebang line (e.g., #!...) or a # [Config] / # [IO] block, you MUST fully preserve them exactly as-is at the very top of the compiled output.

Please compile the draft content provided below:"""


# ---------------------------------------------------------------------------
# spec 驱动的规则生成
# ---------------------------------------------------------------------------
def render_node_rules(spec: Dict[str, Any]) -> str:
    st = spec["sections"]["state"]
    meta = st["metadata"]
    return f"""1. Every Node must be declared as a top-level heading: `{st['heading']}`.
   - If it is the final node, it must have `- **is_final**: true` or `- is_final: true` listed as a bullet point at the top of the node block.
   - All other metadata attributes of the node MUST be preserved as list properties directly under the node heading. Supported metadata keys:
      - **type**: {', '.join(f"`{v}`" for v in meta['type']['enum'])} (default `{meta['type']['default']}`)
      - **tools**: comma-separated tool names (default empty)
      - **src**: path (required when type is `script` or `skill`)
      - **interactive**: `true`/`false` (default `false`)
      - **history_window**: integer (optional)
      - **max_loops**: integer (optional, defaults to global max_loops)"""


def render_transition_rules(spec: Dict[str, Any]) -> str:
    tr = spec["sections"]["state"]["sub_sections"]["transitions"]
    return f"""2. Transition logic must be compiled into a sub-section: `{tr['heading']}`.
   - For multiple conditional transitions (e.g., table or rules), use a Markdown table:
     | Condition | Next Node | Require Approval | Feedback |
     | :--- | :--- | :--- | :--- |
     | expression_1 | Node_A | yes | msg_A |
     | expression_2 | Node_B | no | msg_B |
   - For a single unconditional transition, you can just use a list item:
     - Default -> TargetNode
   - If the user wrote informal transition descriptions or shorthand (e.g., "go to Win", "跳转到 Finish"), translate them into standard list or table transitions.
   - If the user did not write any transition logic for a non-final node, do not output any transitions; the interpreter will automatically fallback to sequential execution."""


def render_section_rules(spec: Dict[str, Any]) -> str:
    s = spec["sections"]
    return f"""3. Preserve the global instructions (text before the first top-level section) and each node's task instructions unchanged, except for formatting them into standard markdown blocks.
4. Recognized top-level sections are: `{s['config']['heading']}` (engine params like `max_loops`), `{s['io']['heading']}` (reader/writer), `{s['tools']['heading']}` (tool declarations), and `{s['state']['heading']}` (nodes). Preserve them exactly as-is.
5. Unknown sections are allowed (source is markdown-first) and must be preserved verbatim as natural language."""


def build_prompt(spec: Dict[str, Any], footer: str) -> str:
    rules = "\n".join(
        [RULES_INTRO, render_node_rules(spec), render_transition_rules(spec), render_section_rules(spec)]
    )
    return f"{HEADER}\n\n{rules}\n\n{footer}"


# ---------------------------------------------------------------------------
# compiler.py 更新
# ---------------------------------------------------------------------------
def _replace_prompt(content: str, new_prompt: str) -> str:
    start = content.index('COMPILER_PROMPT = """')
    end = content.index('"""', start + len('COMPILER_PROMPT = """')) + 3
    return content[:start] + 'COMPILER_PROMPT = """' + new_prompt + '"""' + content[end:]


def update_compiler(prompt: str) -> None:
    content = COMPILER_PATH.read_text(encoding="utf-8")
    updated = _replace_prompt(content, prompt)
    if updated != content:
        COMPILER_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated: {COMPILER_PATH}", file=sys.stderr)
    else:
        print(f"No change: {COMPILER_PATH}", file=sys.stderr)


# ---------------------------------------------------------------------------
# bootstrap_compiled.md 更新（替换 CompileDraft 节点的 instructions）
# ---------------------------------------------------------------------------
def update_bootstrap(skill_prompt: str) -> None:
    content = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    # 用结构化节点边界定位 CompileDraft：节点标题 -> 下一个节点标题
    # 注意不能用 "## [Transitions]" 做 end 标记，因为 prompt 内容里也含该字符串。
    # CompileDraft 的跳转子节（Default -> ValidateSyntax）是固定结构，替换时保留。
    node_marker = "# [Node] CompileDraft"
    next_node_marker = "# [Node] ValidateSyntax"
    transitions_tail = "## [Transitions]\n- Default -> ValidateSyntax\n\n"
    if node_marker not in content or next_node_marker not in content:
        print(f"[Warning] Could not locate CompileDraft node in {BOOTSTRAP_PATH}; skipping.", file=sys.stderr)
        return
    start = content.index(node_marker) + len(node_marker)
    end = content.index(next_node_marker)
    # 替换节点标题与其后指令，保留跳转子节
    updated = content[:start] + "\n" + skill_prompt + "\n\n" + transitions_tail + content[end:]
    if updated != content:
        BOOTSTRAP_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated: {BOOTSTRAP_PATH}", file=sys.stderr)
    else:
        print(f"No change: {BOOTSTRAP_PATH}", file=sys.stderr)


def main() -> None:
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    prompt_py = build_prompt(spec, FOOTER_PY)
    prompt_skill = build_prompt(spec, FOOTER_SKILL)

    if "--stdout" in sys.argv:
        sys.stdout.write('COMPILER_PROMPT = """' + prompt_py + '"""\n')
    else:
        update_compiler(prompt_py)
        update_bootstrap(prompt_skill)


if __name__ == "__main__":
    main()
