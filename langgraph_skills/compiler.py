from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from langgraph_skills.config import Settings, get_deepseek_key

COMPILER_PROMPT = """You are a LangGraph Skill Compiler.
Your task is to read a draft skill written by a human (which might have loose syntax, informal node declarations, or informal transition descriptions) and compile it into a standard Markdown AST state machine format.

Rules:

1. Every Node must be declared as a top-level heading: `# [Node] NodeName`.
   - If it is the final node, it must have `- **is_final**: true` or `- is_final: true` listed as a bullet point at the top of the node block.
   - All other metadata attributes of the node MUST be preserved as list properties directly under the node heading. Supported metadata keys:
      - **type**: `llm`, `code`, `script`, `skill` (default `llm`)
      - **tools**: comma-separated tool names (default empty)
      - **src**: path (required when type is `script` or `skill`)
      - **interactive**: `true`/`false` (default `false`)
      - **history_window**: integer (optional)
      - **max_loops**: integer (optional, defaults to global max_loops)
2. Transition logic must be compiled into a sub-section: `## [Transitions]`.
   - For multiple conditional transitions (e.g., table or rules), use a Markdown table:
     | Condition | Next Node | Require Approval | Feedback |
     | :--- | :--- | :--- | :--- |
     | expression_1 | Node_A | yes | msg_A |
     | expression_2 | Node_B | no | msg_B |
   - For a single unconditional transition, you can just use a list item:
      - Default -> TargetNode            (no message history inheritance)
      - Default ==> TargetNode           (inherit source node's message history)
      - Default ==> TargetNode <==       (inherit + subgraph output replaces parent messages)
   - If the user wrote informal transition descriptions or shorthand (e.g., "go to Win", "跳转到 Finish"), translate them into standard list or table transitions.
   - To mark that the target node should inherit the source node's message history, use `==>` instead of `->` in list form, or prefix the target in the table cell (e.g., `==> TargetNode`). To mark that a subgraph call should replace the parent's messages, use `==> TargetNode <==`.
   - If the user did not write any transition logic for a non-final node, do not output any transitions; the interpreter will automatically fallback to sequential execution.
3. Preserve the global instructions (text before the first top-level section) and each node's task instructions unchanged, except for formatting them into standard markdown blocks.
4. Recognized top-level sections are: `# [Config]` (engine params like `max_loops`), `# [IO]` (reader/writer), `# [Tools]` (tool declarations), `# [Node] NodeName` (nodes), and `# [SubGraph] Name` (subgraphs: `## [Node]` children or `- **src**: path`). Preserve them exactly as-is.
5. Unknown sections are allowed (source is markdown-first) and must be preserved verbatim as natural language.

Output ONLY the compiled Markdown containing the valid `# [Node]` and `## [Transitions]` structures. Do not wrap the output in markdown code blocks unless the input draft itself was wrapped.
If the input draft has a shebang line (e.g., #!...) or a # [Config] / # [IO] block, you MUST fully preserve them exactly as-is at the very top of the compiled output.

Draft Skill:
{draft}
"""

def compile_skill(draft_path: str, output_path: str):
    with open(draft_path, "r", encoding="utf-8") as f:
        draft_content = f.read()

    api_key = get_deepseek_key()
    if not api_key:
        print("Warning: DeepSeek API key not found (set DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE). Skipping LLM compilation step, copying draft as-is.")
        compiled_content = draft_content
    else:
        settings = Settings.from_env()
        llm = ChatOpenAI(
            model=settings.model,
            temperature=settings.temperature,
            api_key=SecretStr(api_key),
            base_url=settings.base_url,
        )
        prompt = ChatPromptTemplate.from_template(COMPILER_PROMPT)
        chain = prompt | llm
        
        print(f"Compiling {draft_path} using DeepSeek...")
        response = chain.invoke({"draft": draft_content})
        compiled_content = str(response.content)
        
        # Remove markdown code blocks if the LLM wrapped it
        if compiled_content.startswith("```"):
            lines = compiled_content.split("\n")
            compiled_content = "\n".join(lines[1:-1])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(compiled_content)
    print(f"Compiled skill saved to {output_path}")
