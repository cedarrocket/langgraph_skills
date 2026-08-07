> [English](./README.md) | 简体中文
> 本项目及本 README 目前由 opencode / DeepSeek V4 Flash 创建。

# LangGraph Skills

一个把 **prompt/LLM Graph 作为一等公民**的小型解释器——可以把它想成 **LLM Agent 界的 Perl**：一门"语言"是 Markdown、而"执行"是图的脚本解析器。你用 Markdown 把 Agent 描述成状态机，解释器把它编译成 LangGraph 图并运行。

**核心目标**：在 DSL 语法尽量简单的同时，完成大部分 agent/harness 功能；把一切硬编码逻辑从解释器里赶出去，改成函数调用或外部嵌入（tools / `type: script` / `type: code`）。

项目最早源于 [_archive/agent_v2.py](_archive/agent_v2.py)：一段硬编码的 Python Agent（对话历史污染、mypy 自修复重试逻辑全写死在代码里）。本项目把这类逻辑搬进声明式 Markdown 脚本，例如 [assistant_compiled.md](assistant_compiled.md)。

---

## 它是什么

- 一门 Markdown DSL：区段（`# [Node]`、`## [Transitions]`…）是图的声明，自由文本就是 prompt
- 单遍解析器（Markdown → IR）+ 运行时构建的 LangGraph 后端
- 一个 CLI（`lgskills`），支持 shebang 直跑 Markdown 文件（`#!/usr/bin/env lgskills`）
- 一个 toy/实验性质的可运行解释器——**不是生产级框架**

## 它不是什么

- 不是带中间件栈的重型 agent 框架——一个小的 DSL、一个运行时
- 不是完整编程语言——DSL 里没有表达式和复杂控制流，那是内嵌 script 的职责（见 [spec/dsl_spec.yaml](spec/dsl_spec.yaml) 的设计基线）
- 不是托管服务——本地单次执行，尚无流式 / checkpoint / 常驻运行时

---

## 功能

### DSL

- **节点类型**：`llm`（默认）、`code`（内联 Python）、`script`（外部文件）、`skill`（`src:` 嵌套 skill）
- **跳转三态**：`->`（不继承消息历史）、`==>`（继承源节点消息历史）、`==> X <==`（继承 + 子图输出整体覆盖父图 messages，用于压缩式子图调用）
- **静态并行**：`- Parallel ==> A, B, C` 扇出到多个节点并行执行；分支自动汇聚到共同 join 节点（validator 会检查死锁）
- **子图**：`# [SubGraph]` 编译为 LangGraph 原生子图节点；支持递归嵌套（内层用 `### [Node]`）
- **上下文压缩**：`max_context_length` + pre_node 检查点——上下文超限时节点提前跳转到压缩子图，子图输出替换父图消息
- **JSON 输出约束**：`## [Output JSON]`——跳转前校验，失败自动退回本节点自修复
- **节点元数据**：`tools` / `interactive` / `history_window` / `max_loops` / `max_context_length`

### 运行时

- **工具**：`# [Tools]` 声明 script/api 工具；`tools/` 目录自动加载 `@tool` 装饰器工具；每图隔离的 `ToolRegistry`；LLM 节点与工具间的 ReAct 闭环
- **配置**：三层 JSON 配置（默认 < 全局 `~/.config/langgraph_skills/config.json` < 项目 `lgskills.json`）；密钥用 `{file:}` / `{env:}` 引用；`lgskills model` 管理 provider/模型
- **消息归属与跨度追踪**：每个节点产出的消息自动带 `metadata`（node/loop）；每次调用记录 span（消息起止索引、类型、prompt 边界、工具调用参数与结果）；代码节点可直接读 `spans`——无需额外 DSL 语法即可逐步审计
- **触发机制**（`triggers.json`）：统一的"条件 → 处理程序"介入层（pre_llm / post_node / on_error 检查点）；处理程序可访问 `deliverables`、`messages`、`transition_to`、`compact()`——不需要中间件栈

### 局限（如实）

- `type: skill` 仍是 `run_skill` 模拟执行（真子图只支持 `# [SubGraph]` 声明）
- 单次执行：无流式、无 checkpoint/恢复、无常驻运行时、无 MCP
- 模型选择是全局的（skill 级），不支持节点级混用
- 运行时没有人工审批门（spec 语法里有该属性，但交互式审批未实现）

---

## 快速开始

```bash
pip install -e .            # 注册 lgskills 命令
```

API 密钥（任选其一）：

```bash
export DEEPSEEK_API_KEY=sk-...            # 或
export DEEPSEEK_API_KEY_FILE=~/sys/keys/deepseek.txt
# 或写入 ~/.config/langgraph_skills/config.json 用 {file:} 引用（推荐）
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

常用命令：

```bash
lgskills validate test_skills/sample_skill.md     # 静态校验
lgskills run test_skills/game_compiled.md "start" # 运行
lgskills model list                                # 查看 provider/模型
```

把 Markdown 当脚本直接跑：

```bash
chmod +x bootstrap_compiled.md
./bootstrap_compiled.md --input_path assistant_draft.md --output_path out.md
```

---

## 一个最简单的例子

两节点的迷你 agent：

````markdown
# [Node] Ask
- **type**: llm

用一句话回答用户问题。

## [Transitions]
- Default -> Done

# [Node] Done
- **is_final**: true
- **type**: code

```python
transition_to(None, "done")
```
````

运行：

```bash
lgskills run ask.md "2+2 等于几？"
```

扇出 / 汇聚的例子（并行分支，自动收敛）：

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

调研该主题，把结论写进你的最终 payload。

## [Transitions]
- Default -> Join

# [Node] Review
- **type**: llm

列出该主题的风险，把结论写进你的最终 payload。

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

压缩的例子（上下文压缩闭环）：

````markdown
# [Node] Work
- **type**: llm
- **max_context_length**: 300

继续任务。

## [Transitions]
- Default ==> Compact <==

# [SubGraph] Compact
## [Node] Summarize
- **type**: llm

把对话历史总结成一句简短的话。

## [Transitions]
- Default -> Apply

## [Node] Apply
- **type**: code

```python
deliverables["_child_messages"] = messages[-1:]  # 只保留摘要
transition_to(None, "compacted")
```
````

当上下文超过 300 字符时，`Work` 在执行前跳转到 `Compact` 子图；子图用摘要替换父图的消息历史。

---

## 工作原理

```
Markdown 源码 ─[parser.py]→ IR (models.py) ─[validator]→ 校验后 IR
      ─[graph.py / build_graph]→ LangGraph 图 ─[executors.py]→ 执行
```

- **前端**（`parser.py` + `models.py`）：Markdown → 结构化 IR，可独立测试；未知区段是 warning 而非 error（源首先是 markdown）
- **执行器**（`executors.py`）：可插拔的节点类型 → 执行函数映射（`register_executor` 扩展新类型）
- **工具**（`tools.py`）：每图隔离注册表，嵌套 skill 互不污染
- **配置**（`config.py`）：环境变量 + 三层 JSON，单一入口
- **spec 驱动**：[spec/dsl_spec.yaml](spec/dsl_spec.yaml) 是 DSL 的唯一真相源——文档、编译器提示词、金标准测试都由它生成

## 项目结构

```
langgraph_skills/
├── langgraph_skills/          # 核心包
│   ├── cli.py                 # CLI 入口 (compile/validate/run/model)
│   ├── config.py              # 三层 JSON 配置 + 密钥引用
│   ├── models.py              # IR 模型 + AgentState（reducer）
│   ├── parser.py              # Markdown -> IR + 静态校验
│   ├── tools.py               # 每图隔离的工具注册表 + 工厂
│   ├── executors.py           # 节点执行器 (llm/code/script/skill)
│   ├── nodes.py               # 节点工厂 + 路由器 + 检查点
│   ├── graph.py               # 图构建（含真子图编译）
│   ├── runner.py              # 运行时 (run_skill/run_cli)
│   ├── triggers.py            # 条件 -> 处理程序介入层
│   └── compiler.py            # 草稿 -> 编译的 LLM 编译器
├── spec/dsl_spec.yaml         # DSL 唯一真相源
├── scripts/                   # 生成文档/编译器提示词/golden IR
├── tests/                     # 测试（150+，含金标准快照）
├── test_skills/               # 示例与测试技能
├── bootstrap_compiled.md      # 自举编译器（shebang 示例）
├── assistant_compiled.md      # 编程助手示例
├── PROCESS.md                 # 维护 SOP（质量门、backlog）
└── README.md / README.en.md   # 本文档（中/英）
```

DSL 语法参考：[skill_syntax_guide.md](skill_syntax_guide.md)。设计讨论与 backlog：[PROCESS.md](PROCESS.md)。

---

## 开发

```bash
pip install -e ".[dev]"
pytest tests/ -q                                      # 测试
mypy langgraph_skills                                 # 类型检查
ruff check langgraph_skills tests scripts             # lint
# 校验所有随附 skill（与 CI 相同循环）：
for skill in *.md test_skills/*.md; do
  case "$skill" in README.md|skill_syntax_guide.md|PROCESS.md|README.en.md) continue;; esac
  lgskills validate "$skill" || exit 1
done
```

修改 DSL 时：先改 `spec/dsl_spec.yaml`，再重新生成（`python scripts/gen_docs.py`、`python scripts/gen_compiler_prompt.py`），必要时更新金标准示例，最后跑全套测试——见 PROCESS.md。

---

## 声明

- 这是一个 toy/实验性质的解释器——**不是**生产级 agent 框架。
- 本项目及本 README 目前由 opencode / DeepSeek V4 Flash 创建。
