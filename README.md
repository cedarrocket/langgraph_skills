> English | [简体中文](./README.en.md)
> This project and this README are currently created by opencode / DeepSeek V4 Flash.

# LangGraph Skills

A small interpreter that treats **prompt/LLM graphs as first-class citizens** — think of it as a **logos/script/Perl for LLM agents**: a script parser whose "language" is Markdown, and whose "execution" is a graph. You describe the agent as a state machine in Markdown; the interpreter compiles it into a LangGraph graph and runs it.

The core goal: **attempt to keep the DSL syntax as simple as possible while covering common agent/harness functionality**; and attempt to drive hard-coded logic out of the LLM DSL, converting it into function calls or external embedding (tools calling / `type: script` / `type: code` / `pyfunction`).

It first came from a simple, beginner-level agent snippet [_archive/agent_v2.py](_archive/agent_v2.py): a hard-coded Python agent (prompt-history pollution, mypy self-healing retry logic all baked in).

---

## What it is

- A Markdown DSL where sections (`# [Node]`, `## [Transitions]`, …) are graph declarations, and free-form text is prompt
- A single-pass parser (Markdown → IR) plus a LangGraph backend built at runtime
- A CLI (`lgskills`) that can run Markdown files directly via shebang (`#!/usr/bin/env lgskills`)
- A toy/experimental codebase — but it is a working interpreter

## What it is not

- Not a heavyweight agent framework with middleware stacks — one small DSL, one runtime
- Not a full programming language — no expressions or complex control flow in the DSL; that belongs to embedded scripts (see design principles in [spec/dsl_spec.yaml](spec/dsl_spec.yaml))
- Not a hosted service — it runs locally, single-shot, no streaming / checkpoint / resident runtime yet

---

## Features

### DSL

- **Node types**: `llm` (default), `code` (inline Python), `script` (external file), `skill` (nested skill via `src:`)
- **Transitions**: `->` (no history inheritance), `==>` (inherit source node's message history), `==> X <==` (inherit + replace parent messages — for compression-style subgraph calls)
- **Static parallelism**: `- Parallel ==> A, B, C` fans out to multiple nodes; branches converge automatically at a common join node (deadlock-checked at validation time)
- **Subgraphs**: `# [SubGraph]` compiles to native LangGraph subgraph nodes; recursive nesting supported (`### [Node]` for nested levels)
- **Context compaction**: `max_context_length` + pre_node checkpoint — when context exceeds the threshold, the node redirects early to a compression subgraph, which replaces parent messages
- **JSON output schema**: `## [Output JSON]` — validated before transition; on failure the node routes back to itself for self-healing
- **Node metadata**: `tools`, `interactive`, `history_window`, `max_loops`, `max_context_length`

### Runtime

- **Tools**: `# [Tools]` declares script/api tools; `tools/` directories auto-load `@tool`-decorated Python tools; per-graph isolated `ToolRegistry`; ReAct loop between LLM nodes and tools
- **Configuration**: three-layer JSON config (default < global `~/.config/langgraph_skills/config.json` < project `lgskills.json`); secrets referenced via `{file:}` / `{env:}`; `lgskills model` manages providers/models
- **Message provenance & spans**: every node's output messages carry `metadata` (node/loop); every invocation records a span (`start`/`end` message indices, type, prompt boundary, tool-call args/results); code nodes can read `spans` directly — step-by-step auditability without extra DSL syntax
- **Triggers** (via `triggers.json`): unified "condition → handler" hook layer at pre_llm / post_node / on_error checkpoints; handlers get `deliverables`, `messages`, `transition_to`, `compact()` — no middleware stack needed

### Limitations (honest)

- `type: skill` still uses `run_skill` simulation, not a native subgraph (true subgraphs only via `# [SubGraph]`)
- Single-shot execution: no streaming, no checkpointing/resume, no resident runtime, no MCP
- Model selection is global (skill-level), not per-node
- No human-approval gate in the runtime (a parsed attribute exists in the spec grammar, but the interactive gate is not implemented)

---

## Quick start

```bash
pip install -e .            # registers the lgskills command
```

API key (one of):

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
lgskills model list                                # list providers/models
```

Run a Markdown file as a script:

```bash
chmod +x bootstrap_compiled.md
./bootstrap_compiled.md --input_path assistant_draft.md --output_path out.md
```

---

## A first example

A minimal two-node agent:

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

Run it:

```bash
lgskills run ask.md "What is 2+2?"
```

A fan-out / join example (parallel branches, auto-converge):

````markdown
# [Node] Split
- **type**: code

```python
transition_to(["Research", "Review"], "x")
```

## [Transitions]
- Parallel ==> Research, Review

# [Node] Research
- **type**: llm

Research the topic; write your findings to deliverables["research"] in your final payload.

## [Transitions]
- Default -> Join

# [Node] Review
- **type**: llm

List the risks of the topic; write them to deliverables["risks"] in your final payload.

## [Transitions]
- Default -> Join

# [Node] Join
- **is_final**: true
- **type**: code

```python
combined = deliverables.get("payload", "")
transition_to(None, combined)
```
````

A compression example (context compaction loop):

````markdown
# [Node] Work
- **type**: llm
- **max_context_length**: 300

Continue the task.

## [Transitions]
- Default ==> Compact <==

# [SubGraph] Compact
## [Node] Summarize
- **type**: llm

Summarize the conversation history into one short message.

## [Transitions]
- Default -> Apply

## [Node] Apply
- **type**: code

```python
deliverables["_child_messages"] = messages[-1:]  # keep only the summary
transition_to(None, "compacted")
```
````

When the context exceeds 300 chars, `Work` redirects to the `Compact` subgraph before running; the subgraph replaces the parent's message history with the summary.

---

## How it works

```
Markdown source ─[parser.py]→ IR (models.py) ─[validator]→ validated IR
      ─[graph.py / build_graph]→ LangGraph graph ─[executors.py]→ execution
```

- **Frontend** (`parser.py` + `models.py`): Markdown → structured IR, independently testable; unknown sections are warnings, not errors (the source is markdown first)
- **Executors** (`executors.py`): pluggable node-type → executor mapping (`register_executor` to add new types)
- **Tools** (`tools.py`): per-graph isolated registry; nested skills don't pollute each other
- **Config** (`config.py`): env vars + three-layer JSON, single entry point
- **Spec-driven**: [spec/dsl_spec.yaml](spec/dsl_spec.yaml) is the single source of truth — docs, compiler prompt and golden tests are all generated from it

## Project layout

```
langgraph_skills/
├── langgraph_skills/          # core package
│   ├── cli.py                 # CLI entry (compile/validate/run/model)
│   ├── config.py              # three-layer JSON config + secret refs
│   ├── models.py              # IR models + AgentState (reducers)
│   ├── parser.py              # Markdown -> IR + static validation
│   ├── tools.py               # per-graph tool registry + factories
│   ├── executors.py           # node executors (llm/code/script/skill)
│   ├── nodes.py               # node factory + routers + checkpoints
│   ├── graph.py               # graph building (incl. true subgraphs)
│   ├── runner.py              # runtime (run_skill/run_cli)
│   ├── triggers.py            # condition -> handler hook layer
│   └── compiler.py            # LLM compiler for loose draft -> compiled
├── spec/dsl_spec.yaml         # single source of truth for the DSL
├── scripts/                   # gen docs / compiler prompt / golden IR
├── tests/                     # tests (150+, incl. golden snapshot)
├── test_skills/               # example & test skills
├── bootstrap_compiled.md      # self-bootstrapping compiler (shebang demo)
├── assistant_compiled.md      # coding-assistant example
├── PROCESS.md                 # maintenance SOP (quality gates, backlog)
└── README.md / README.en.md   # this doc (CN/EN)
```

DSL syntax reference: [skill_syntax_guide.md](skill_syntax_guide.md). Design discussions and backlog: [PROCESS.md](PROCESS.md).

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -q                                      # tests
mypy langgraph_skills                                 # type check
ruff check langgraph_skills tests scripts             # lint
# validate all shipped skills (same loop as CI):
for skill in *.md test_skills/*.md; do
  case "$skill" in README.md|skill_syntax_guide.md|PROCESS.md|README.en.md) continue;; esac
  lgskills validate "$skill" || exit 1
done
```

After changing the DSL: edit `spec/dsl_spec.yaml` first, then regenerate (`python scripts/gen_docs.py`, `python scripts/gen_compiler_prompt.py`), update golden examples if needed, and run the full suite — see PROCESS.md.

---

## Notice

- A toy/experimental interpreter — still evolving.
- This project and this README are currently created by opencode / DeepSeek V4 Flash.
