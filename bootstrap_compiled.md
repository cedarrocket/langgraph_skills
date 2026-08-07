#!/usr/bin/env lgskills

# [Config]
- **reader**: txt_reader
- **writer**: txt_writer

You are a LangGraph Skill Compiler.

# [Node] CompileDraft
You are a LangGraph Skill Compiler.
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
      - **max_context_length**: integer (optional; when the entering context exceeds this, the node redirects early to its `==>` subgraph without running)
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

Please compile the draft content provided below:

## [Transitions]
- Default -> ValidateSyntax

# [Node] ValidateSyntax
- **type**: script
- **src**: validate_syntax.py

## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| validation fails | FixCompilation | no | |
| validation passes | Finish | yes | |

# [Node] FixCompilation
Your previous compilation attempt failed validation.
Please correct the compiled state machine code according to the validation error.

Instructions:
1. Analyze the original draft (in `input_path_content`) and your failed compilation attempt (in Context).
2. Examine the validation error (in `validation_error`).
3. Correct the node declarations, transitions, or is_final flags to ensure it matches the new syntax guidelines.
4. Output ONLY the corrected compiled Markdown state machine. Do not explain, do not wrap in code blocks.

## [Transitions]
- Default -> ValidateSyntax

# [Node] Finish
- **type**: code
- **is_final**: true

```python
transition_to(None, get_payload())
```