> 本项目及本 README 目前由 opencode / DeepSeek V4 Flash 创建。功能与文档仍在演进，欢迎指正。

# LangGraph Skills

一个用 Markdown 描述 LLM Agent 状态机的**简易解释器**（toy 级实现）。它把用 Markdown 写的、带特定区段（`# [Node]`、`## [Transitions]` 等）的脚本解析为结构化 IR，再交给 LangGraph 在运行时动态构建图并执行。

定位：**可以 shebang 直跑的实验性项目**。它不追求成为生产级 agent 框架，而是验证"声明式 Markdown 状态机 + LangGraph"这条路是否可行——写起来像脚本，跑起来是图。

最早的设计动机来自 [_archive/agent_v2.py](_archive/agent_v2.py)：一段硬编码的 Python Agent 脚本（对话历史污染、mypy 自修复重试逻辑全写死在代码里）。本项目把这类逻辑搬进 Markdown，用 [assistant_compiled.md](assistant_compiled.md) 这类声明式脚本替代。

---

## 当前具备的能力

- **声明式状态机**：`# [Node]` 声明节点，`## [Transitions]` 声明跳转（`->` / `==>` / `==> X <==` 三态），支持 LLM / code / script / skill 四种节点类型
- **嵌套子图**：`# [SubGraph]` 声明子图，编译为 LangGraph 原生子图节点（`add_node(subgraph)`），子图与父图共享 state，可递归嵌套
- **上下文压缩闭环**（实验性）：`max_context_length` 元数据 + pre_node 检查点，上下文超限时提前跳转到压缩子图，压缩结果通过 `_child_messages` 协议替换回父图
- **静态并行**：`- Parallel ==> A, B, C` 扇出到多个节点并行执行，多分支自动汇聚到共同 join 节点，`deliverables` 按字段合并
- **消息归属与跨度追踪**：每个节点产出消息自动打 `metadata`（node/loop），每次调用记录 span（start/end 索引、类型、prompt 边界、工具调用参数与结果），代码节点内可直接读 `spans`
- **JSON Schema 校验与自修复**：`## [Output JSON]` 声明输出约束，校验失败自动回退原节点修正
- **人工审批门**：跳转可声明 `[Require Approval]`，终端交互确认或拒绝
- **工具注册**：`# [Tools]` 声明 script/api 工具，`tools/` 目录动态加载 `@tool` 装饰器工具，每图独立 ToolRegistry
- **配置**：三层 JSON 配置（默认 < 全局 `~/.config/langgraph_skills/config.json` < 项目 `lgskills.json`），密钥用 `{file:}`/`{env:}` 引用，`lgskills model` 子命令管理模型
- **Shebang 直跑**：Markdown 头部 `#!/usr/bin/env lgskills` + `chmod +x` 后可直接执行

**已知局限（如实说明）**：
- `type: skill` 节点仍是 `run_skill` 模拟执行（不是 LangGraph 子图）；真子图只支持 `# [SubGraph]` 声明
- 无流式输出、无 checkpoint/恢复、无常驻运行时、无 MCP——单轮执行模型
- 模型选择目前是全局配置（skill 级），不支持节点级混用

---

## 快速开始

```bash
pip install -e .            # 注册 lgskills 命令
```

配置 API 密钥（三选一）：

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
lgskills model list                                # 查看模型
```

直接当脚本跑：

```bash
chmod +x bootstrap_compiled.md
./bootstrap_compiled.md --input_path assistant_draft.md --output_path out.md
```

一个最小示例：

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

---

## 项目结构

```
langgraph_skills/
├── langgraph_skills/          # 核心包
│   ├── cli.py                 # CLI 入口（compile/validate/run/model）
│   ├── config.py              # 三层 JSON 配置 + 密钥引用
│   ├── models.py              # IR 数据模型 + AgentState（reducer）
│   ├── parser.py              # Markdown -> IR + 静态校验
│   ├── tools.py               # 每图隔离的工具注册表
│   ├── executors.py           # 节点执行器（llm/code/script/skill）
│   ├── nodes.py               # 节点工厂 + 路由器 + 检查点
│   ├── graph.py               # 图构建（含真子图编译）
│   ├── runner.py              # 运行时（run_skill/run_cli）
│   └── triggers.py            # 触发机制（condition -> handler）
├── spec/dsl_spec.yaml         # DSL 语法唯一真相源
├── scripts/                   # 从 spec 生成文档/编译器提示词/golden IR
├── tests/                     # 测试（151+，含 golden 快照测试）
├── test_skills/               # 示例与测试技能
├── bootstrap_compiled.md      # 自举编译器（shebang 示例）
├── assistant_compiled.md      # 编程助手示例
└── PROCESS.md                 # 项目维护流程（SOP）
```

DSL 语法见 [skill_syntax_guide.md](skill_syntax_guide.md)（由 `spec/dsl_spec.yaml` 生成，人工定稿）。架构与设计讨论记录在 [PROCESS.md](PROCESS.md)。

---

## 开发

```bash
pip install -e ".[dev]"
pytest tests/ -q                 # 测试
mypy langgraph_skills            # 类型检查
ruff check langgraph_skills tests scripts   # lint
```

修改 DSL 语法后需要重新生成文档/编译器提示词：`python scripts/gen_docs.py`、`python scripts/gen_compiler_prompt.py`（见 PROCESS.md 的质量门）。

---

## 声明

- 本项目是一个学习/实验性质的小型解释器，**不是**生产级 agent 框架。
- 本项目及本 README 目前由 opencode / DeepSeek V4 Flash 创建。
