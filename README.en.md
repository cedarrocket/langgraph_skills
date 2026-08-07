> This project and this README are currently created by opencode / DeepSeek V4 Flash. Both the features and docs are still evolving.

# LangGraph Skills

A **toy-level interpreter** that describes LLM Agent state machines in Markdown. It parses Markdown scripts with specific sections (`# [Node]`, `## [Transitions]`, etc.) into a structured IR, then builds and executes a graph at runtime via LangGraph.

Positioning: an **experimental, shebang-runnable project**. It does not aim to be a production-grade agent framework. Its purpose is to validate whether "declarative Markdown state machines + LangGraph" is a viable path — written like a script, executed like a graph.

It originated from [_archive/agent_v2.py](_archive/agent_v2.py): a hard-coded Python agent script (prompt-history pollution, mypy self-healing retry logic all baked into code). This project moves such logic into Markdown, e.g. [assistant_compiled.md](assistant_compiled.md).

---

## Current capabilities

- **Declarative state machine**: `# [Node]` declares nodes, `## [Transitions]` declares jumps (`->` / `==>` / `==> X <==`), with four node types: llm / code / script / skill
- **Nested subgraphs**: `# [SubGraph]` compiles to a native LangGraph subgraph node (`add_node(subgraph)`); subgraph shares state with parent, supports recursive nesting
- **Context compaction loop** (experimental): `max_context_length` metadata + pre_node checkpoint; when context exceeds the threshold, redirect early to a compression subgraph, and the result replaces parent messages via the `_child_messages` protocol
- **Static parallelism**: `- Parallel ==> A, B, C` fans out to multiple nodes running in parallel; branches converge automatically at a common join node; `deliverables` merge field-wise
- **Message provenance & span tracking**: every node's output messages are auto-tagged with `metadata` (node/loop); each invocation records a span (start/end indices, type, prompt boundary, tool-call args and result); code nodes can read `spans` directly
- **JSON Schema validation & self-healing**: `## [Output JSON]` declares output constraints; on failure the node routes back to itself for correction
- **Human approval gate**: transitions can declare `[Require Approval]`; interactive confirmation or rejection in the terminal
- **Tool registration**: `# [Tools]` declares script/api tools; `tools/` directory auto-loads `@tool` decorated tools; per-graph isolated ToolRegistry
- **Configuration**: three-layer JSON config (default < global `~/.config/langgraph_skills/config.json` < project `lgskills.json`); secrets referenced via `{file:}`/`{env:}`; `lgskills model` subcommand manages models
- **Shebang execution**: `#!/usr/bin/env lgskills` at the top of a Markdown file + `chmod +x` runs it directly

**Known limitations (honest)**
- `type: skill` nodes still use `run_skill` simulation (not a LangGraph subgraph); true subgraphs only via `# [SubGraph]`
- No streaming, no checkpointing/resume, no resident runtime, no MCP — single-shot execution model
- Model selection is global (skill-level), not per-node

---

## Quick start

```bash
pip install -e .            # registers the lgskills command
```

Configure your API key (pick one):

```bash
export DEEPSEEK_API_KEY=sk-...            # or
export DEEPSEEK_API_KEY_FILE=~/sys/keys/deepseek.txt
# or write ~/.config/langgraph_skills/config.json using {file:} (recommended)
```

```json
{
  "model": "deepseek/deepseek-chat",
  "provider": {
    "deepseek": {
      "options": {
        "apiKey": "{file:~/sys/keys/deepseek.txt}",
        "baseURL": "https://api.deepseek.com/v1"
      }
    }
  }
}
```

Common commands:

```bash
lgskills validate test_skills/sample_skill.md     # static validation
lgskills run test_skills/game_compiled.md "start" # run
lgskills model list                                # list models
```

Run as a script:

```bash
chmod +x bootstrap_compiled.md
./bootstrap_compiled.md --input_path assistant_draft.md --output_path out.md
```

A minimal example:

````markdown
# [Node] Ask
- **type**: llm

Answer the user's question in one sentence.

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
- **type**: code

```python
transition_to(None, "done")
```
````

---

## Project layout

```
langgraph_skills/
├── langgraph_skills/          # core package
│   ├── cli.py                 # CLI entry (compile/validate/run/model)
│   ├── config.py              # three-layer JSON config + secret refs
│   ├── models.py              # IR models + AgentState (reducers)
│   ├── parser.py              # Markdown -> IR + static validation
│   ├── tools.py               # per-graph tool registry
│   ├── executors.py           # node executors (llm/code/script/skill)
│   ├── nodes.py               # node factory + routers + checkpoints
│   ├── graph.py               # graph building (incl. true subgraphs)
│   ├── runner.py              # runtime (run_skill/run_cli)
│   └── triggers.py            # trigger mechanism (condition -> handler)
├── spec/dsl_spec.yaml         # single source of truth for the DSL
├── scripts/                   # generate docs / compiler prompt / golden IR
├── tests/                     # tests (151+, incl. golden snapshot tests)
├── test_skills/               # example & test skills
├── bootstrap_compiled.md      # self-bootstrapping compiler (shebang example)
├── assistant_compiled.md      # coding-assistant example
└── PROCESS.md                 # project maintenance SOP
```

DSL syntax: [skill_syntax_guide.md](skill_syntax_guide.md) (generated from `spec/dsl_spec.yaml`, hand-finalized). Architecture and design discussions live in [PROCESS.md](PROCESS.md).

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -q                 # tests
mypy langgraph_skills            # type check
ruff check langgraph_skills tests scripts   # lint
```

After changing the DSL syntax, regenerate docs/compiler prompt: `python scripts/gen_docs.py`, `python scripts/gen_compiler_prompt.py` (see quality gates in PROCESS.md).

---

## Notice

- This is a learning/experimental toy interpreter, **not** a production-grade agent framework.
- This project and this README are currently created by opencode / DeepSeek V4 Flash.
