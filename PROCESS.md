# 项目维护流程（Process）

> 此文档由 LLM 辅助生成，后续会更新人工版本。

> 本文档是项目的**标准操作流程（SOP）**记录。凡是"未来应当遵循的标准步骤"、
> 自动化/半自动化工具及其用法、踩过的坑与排查方法，都统一记录在这里。
> 新增约定请追加到对应小节，保持单一事实来源。

## 1. 核心原则（设计基线）

- 本项目是**命令行工具**，不是库。"markdown = 小程序语言，langgraph_skills = 它的解释器"。
- DSL 是**密集 prompt 型 LLM 脚本语言**：不引入表达式或复杂流程控制（那是内嵌 script 的职责）。
- 源文件**首先是 markdown**，允许自然语言混入；未知 section 是 warning 而非 error。
- 进程 I/O 边界（stdin/stdout/tty/exit code）**不进 DSL**，归 runner 层。
- 功能分区看齐编译器：词法(行分类) → 语法 → 语义 → 后端 → 运行时；**模块边界 = 推倒边界**。
- 长期可维护、将来能整体推倒重写，不留卡死推倒的技术债。
- **性能结论：Python 不构成瓶颈，保持 Python 不迁移。**
  本项目以 LLM API 调用为主（秒级延迟），Python 解释器开销（微秒~毫秒）低 3~6 个数量级；
  `type: code/script` 节点多用于调工具/调 LLM/轻量处理，属 IO 绑定，非 CPU 密集。
  GIL 在 IO 等待时释放，asyncio/线程池可并行数百 agent。迁 Rust 会摧毁 `type: script`
  直接写 Python 的便利性且收益微乎其微。未来若出现"大量纯 CPU 计算型 code 节点且吞吐极高"
  才需重新评估；否则优化方向是解析/构图缓存、asyncio 并发、消息历史裁剪。

## 2. 变更标准流程（SOP）

### 2.1 重构/大改动的标准步骤

任何涉及 DSL 语法、架构、模块拆分的改动，**先讨论定案，再动代码**：

1. **讨论定案**：先用计划（plan）把方案讲清、逐点拍板，不直接改代码。
2. **更新 spec**：改 `spec/dsl_spec.yaml`（DSL 唯一真相源）。
3. **重新生成参考文档**：`python scripts/gen_docs.py`（产出参考版 `skill_syntax_guide.md`）。
4. **手动定稿最终文档**：基于参考版手动写/润色正式文档；**语法事实必须与 spec 一致**。
5. **更新金标准示例**：改/新增 `spec/examples/*.md` 及对应期望 IR（`.ir.json`）。
6. **落地实现**：按单子逐条实现，每条实现完立即跑质量门（见 §5）。
7. **验证**：`validate` 全部技能 + 测试 + lint + typecheck + build。

### 2.2 DSL 语法变更的最小检查清单

- [ ] `spec/dsl_spec.yaml` 已同步
- [ ] `scripts/gen_docs.py` 已重新生成参考文档
- [ ] `skill_syntax_guide.md`（正式版）已手动更新
- [ ] 金标准示例已覆盖新语法 / 旧示例未失效
- [ ] compiler prompt（`compiler.py` 内 COMPILER_PROMPT）与 spec 一致
- [ ] 全部 `.md` 技能通过 `lgskills validate`

### 2.3 文档生成的工作方式

- `scripts/gen_docs.py` 是**参考文档生成器**，产出骨架供参考，**不是最终文档**。
- 最终文档（`skill_syntax_guide.md` 等）由人手动定稿，保证可读性与完整性。
- 不要因为"能生成"就省略手动润色；也不要让脚本承担超出语法的教学职责。

## 3. 自动化 / 半自动化工具清单

| 工具/命令 | 用途 | 用法 | 状态 |
|---|---|---|---|
| `scripts/gen_docs.py` | spec → 参考文档 | `python scripts/gen_docs.py`（加 `--stdout` 可预览） | 参考用，最终手动定稿 |
| `scripts/gen_compiler_prompt.py` | spec → 编译器 prompt（python 版 compiler.py + skill 版 bootstrap_compiled.md，保证同构） | `python scripts/gen_compiler_prompt.py` | 已启用，随 spec 变更重新生成 |
| `scripts/dump_ir.py` | 现有 parser → IR 初稿（金标准方案 A 生成器） | `python scripts/dump_ir.py <skill.md> [-o out.json]` | 初稿生成，最终契约人工审 |
| `lgskills validate` | 静态校验技能 | `lgskills validate <skill.md>` | 已启用 |
| `lgskills model` | AI 模型/provider 管理（list/set/config/import-opencode） | `lgskills model <list\|set\|config\|import-opencode>` | 已启用 |
| CI (`ci.yml`) | lint/mypy/test/validate/build | 推送时自动 | 已启用 |
| `pip wheel . --no-deps` | 构建 wheel | `python -m pip wheel . --no-deps -w <dir>` | 已启用 |

> **AI 模型配置**：JSON 配置文件（全局 `~/.config/langgraph_skills/config.json` + 项目 `lgskills.json`），
> 密钥用 `{file:path}`/`{env:VAR}` 引用（不写入配置文件），对齐 opencode 惯例。详见 README §2.2。

> 后续新增的自动化/半自动化流程（例如：金标准示例批量生成、IR 对照工具、
> 编译器 prompt 生成、调试复现脚本）统一登记到本表并附使用说明。

### 图可视化（可选调试工具，非核心功能）

`build_graph(skill).get_graph()` 可导出 LangGraph 图拓扑，用于调试/可视化（**玩具级**，不作为框架功能依赖）：

| 方法 | 依赖 | 说明 |
|---|---|---|
| `draw_png()` | `pygraphviz` | `pip install pygraphviz`（wheel 自带 graphviz 运行库，无需系统 apt） |
| `draw_mermaid()` | 无 | 文本图，`graph TD ...` 输出，可直接粘贴到 mermaid 渲染 |
| `draw_ascii()` | `grandalf` | `pip install grandalf`；**注意** grandalf 对含自环（self-loop）的技能会报几何错误 |

> 图可视化**不加入项目依赖**（避免拉重依赖），需要时按上表单独安装。

## 4. 调试与故障排查记录

> 预留：把踩过的坑、排查命令、解决方案追加在这里，避免重复踩。

## 5. 质量门（提交/合并前必跑）

```bash
python -m pytest tests/ -q            # 单元测试
python -m mypy langgraph_skills       # 类型检查
python -m ruff check langgraph_skills tests  # lint
# 全部技能校验（OK 走 stderr，用 exit code 判断）
for f in *.md test_skills/*.md; do
  case "$f" in README.md|skill_syntax_guide.md|PROCESS.md) continue;; esac
  lgskills validate "$f" >/dev/null 2>&1 || { echo "FAIL: $f"; exit 1; }
done
python -m pip wheel . --no-deps -w /tmp/wheeltest  # 构建
```

> 说明：`_archive/`、`build/`、`.conda/`、`__pycache__/`、`*.egg-info/` 等已在
> `.gitignore` 中，不入库。

## 7. Backlog（未来规划，非当前优先）

以下为讨论确认的**未来方向**，当前均不实现，按触发时机评估。**除非有真实使用场景催生，否则不动手。**

### 7.1 常驻运行时（类 REPL / 长期 harness）

> 目标：把 langgraph_skills 从"一次性解释器"演进为"可常驻的 harness 运行时"（类比 openswe/vim）。
> 战略评估：这是**架构级演进**（长驻进程 + 完整状态暴露 + 有状态外部通信），不是小补丁。当前轻量一次性架构（A）与长驻运行时（B）在进程模型、状态可见性、外部调用、内存管理上冲突，需先决定路线再立项。

- **类 REPL 常驻**（可行，成本低）：`run_skill` 外层加 `while True` 循环，跑完一轮等 stdin 再继续。**内核（parser/executors/graph）零改动**——印证"A 内核可被 B 外壳复用"。触发时机：需要跨轮持续交互时。
- **取消特定节点循环上限**（可行，成本低-中）：`NodeInfo.max_loops` 字段已存在（默认 None=继承全局），但 `generic_router` 未使用。需让 router 读**当前节点**的 max_loops（null=继承 / 0 或 -1=无限 / N=独立上限）。触发时机：REPL 节点需要无限循环时。
- **config 阶段启动外部驻留进程 + 运行时跨进程通信**（可行，成本中-高，**仅作 todo**）：生命周期管理（run_skill 结束前统一关闭）+ 通信协议（推荐 JSON 行协议 stdin/stdout 管道，跨进程"类 deliverables"）+ 进程注册表挂到 ExecutorContext（模式同 ToolRegistry）。
  - **暂不实现**：一切临时跨进程需求用 `type: script` 内联 subprocess 代码完成（**可行性已验证**：script 节点在解释器进程内 exec，可用 subprocess/管道轮询外部源；局限是每次冷启动子进程、长轮询阻塞节点，性能待未来解决）。
  - **通信双向性结论（已定）**：双向以 **runner 层事件循环轮询（拉）** 为主——外部进程写队列/文件/管道，runner 轮询后作为**新一轮输入**喂给 graph；**不做**"外部进程主动推入运行中的 graph"（那会破坏 graph 的纯函数契约，且 openswe/pi/opencode 档需求均可由"事件→新轮输入"覆盖）。graph 内核保持纯净，轮询与调度归 runner（B 外壳）。
  - **架构方向结论（已定）**：**常驻运行时外置，解释器保持纯函数微内核**。外部 harness（常驻）通过 `lgskills run --json --resume <state.json>` 反复调用内核；内核保持"每次独立执行、无状态、纯函数"（现状已满足）。这**不破坏**"微内核 + 外部调度"设计——微内核的调度是图内调度（节点流转），外部调度的调度是图间调度（会话组织），两层次不重叠；微内核架构的宿主本就应常驻（类比 LLM+harness / DB+服务 / vim+script）。
  - **判据（防设计倒退）**：内核每次调用独立、无状态、纯函数；**不得**在内核内加 session/连接/常驻循环。外部宿主负责状态持久化、并发、事件循环、交互。

### 7.2 迈向 harness 的能力差距清单（对标 opencode）

> 战略评估：差距分**两个战略层**——①"常驻运行时"层是架构跳变（整体立项，不零散实现）；②"功能增强"层是增量（可在现有架构上添加）。

**第一层：常驻运行时（互相咬合，整体立项）——对应 §7.1**
- 6. 消息无界累积：`AgentState.messages` 用 add_messages 无限累积，无压缩/摘要/裁剪。opencode 有 compaction。
- 8. 并行/事件驱动缺失：执行同步阻塞（`app.stream()`），单线程，无 asyncio/事件循环/后台任务。
- 9. 无流式输出：`execute_llm` 用 `llm.invoke()`，无 `stream()` 逐 token。
- 10. 无交互式 TUI/多路输入：只有 `safe_input`（stdin），无快捷键/多会话切换/历史。
- 5. 无本地保存对话 + 中断恢复：无 checkpoint。**注**：LangGraph 原生支持 checkpointer；且"从特定步骤恢复"在外部宿主 + `--resume` 协议下由宿主负责（保存状态传给内核）。

**第二层：功能增强（增量，不改变运行时模型）**
- 1. 运行时动态切换 model（opencode 的 /model 交互式选择）——配置文件已完成，缺交互切换。
- 7. 无 MCP（Model Context Protocol）——工具生态封闭，是 harness 级关键能力。
- 12. 节点间数据契约弱：只靠 `deliverables["payload"]`（单字符串），无结构化类型传递。
- 2. subagent 并行执行/管理——现无并行；临时可用 `type: script`+线程/子进程绕（性能受限），正式需并行调度。
- 3. memory/truncate 管理——有 `history_window`（切片），但无主动 compaction；script 节点只能读 `messages` 不能结构化改写。
- 4. 程序化条件跳转——condition 目前仅作 LLM 提示（`has_conditional_transitions`），**无程序化求值**；与"DSL 不引入表达式"原则冲突，需设计权衡（可走"DSL 条件引用外部 script/pipe 函数返回判断值"而非 DSL 内表达式）。

### 7.3 配置与外部语法（战略方向）

- 允许 config / tool 设置从 markdown 解耦到 YAML/JSON 等标准格式（当前 `# [Config]`/`# [IO]`/`# [Tools]` 用 markdown 列表，单机 MVP 够用）。
- config 路径支持：1) 默认文件路径，允许字头协议（如 `redis://127.0.0.1:6379/agent_config_key`）；2) 系统变量展开（如 `${AGENT_CONFIG_URI}`）。
- **触发时机**：出现真实多 agent / 分布式配置需求时。当前 markdown config 不阻塞。

### 7.4 其它（低优先 / 长期搁置）

- **langgraph 特性微调**（如 reducer、checkpoint 配置）：power-user 功能，普通用户用不到，长期搁置。
- **userid 选项**：功能极小（config 字段 + 注入 deliverables），但价值依赖 §7.3 的配置加载机制，随 §7.3 做。
- **图可视化依赖**（pygraphviz 等）：见 §3 图可视化说明，不加入项目依赖。

### 7.5 性能结论（已定，防将来纠结）

Python 不构成瓶颈，保持 Python 不迁移（详见 §1 核心原则）。优化方向是解析/构图缓存、asyncio 并发、消息历史裁剪，而非换语言。

### 7.6 Boundaries/Trigger 实现计划书（已确认，待实施）

> 对标 openswe（langchain-ai/open-swe）调研结论：其 12 层 middleware 栈（洋葱模型）实为"操作级介入"的完整形态；
> 我们将其简化为"统一触发器 + 外部处理程序"（业务逻辑外置，复用 type:script 注入模式）。
> 架构原则：骨架用显式状态机（我们的差异化），血肉交给 LLM，介入走 trigger。
> **实现状态：核心机制已完成**（triggers.py + 检查点埋点 + triggers.json 加载 + 108 测试全绿）。

#### 设计决策（全部已锁定）

| # | 决策 |
|---|---|
| 0 | 独立 JSON 文件：`triggers.json`（全局 `~/.config/langgraph_skills/` + 项目根，复用三层配置合并） |
| 1 | MVP 不做表达式暴露限制，直接检查所有变量；**留安全管理架子**（ALLOWED_AST_NODES + 空实现 allowlist） |
| 2 | pyfunction 条件返回 **True 即触发** |
| 3 | 语法糖（max_loops/history_window/require_approval）保留 DSL；JSON 存在时允许语法糖缺失（JSON 优先） |
| 4 | MVP 允许一切函数调用；**留安全管理架子** |
| 5 | condition 用 Python 条件表达式（如 `context_length > 5000`）或 `pyfunction:xxx.py` |
| 6 | 检查点**隐式映射**（用户不声明）：context_length→pre_llm、loop_count→post_node、error_flag→on_error、自定义→pre_llm |

#### triggers.json 格式

```json
{
  "triggers": [
    { "condition": "context_length > 5000", "on_trigger": "handle_overlong.py" },
    { "condition": "pyfunction:check_grade.py", "on_trigger": "handle_grade.py" }
  ]
}
```

#### 语法糖 → 内置 trigger 展开

```
max_loops: 5        → {post_node, "loop_count > 5", 内置 force_END}
history_window: 10  → {pre_llm, "context_length > 10", 内置 slice}
require_approval    → {on_transition, 内置 safe_input 审批}
```

#### 触发处理程序

- 复用 type:script 执行（读 on_trigger 文件，注入 deliverables/messages/get_payload/transition_to）
- 处理程序自行决定：改状态 / 拦截（不跳转）/ 重试（transition_to 本节点）/ 压缩
- **`compact(keep_last)` 已实现**：注入 handler/pyfunction 环境，切片赋值安全裁剪 messages（真实作用于图状态；`messages = [...]` 重新赋值不生效是易踩的坑，用 compact 避免）

#### 表达式静态检查（解析时，非运行时）

```python
def check_condition_expr(expr, scope_vars):
    tree = ast.parse(expr, mode="eval")     # 语法错误 → ParseError
    # 收集 Name 节点 vs scope_vars（所有已定义变量）→ 未定义 → ParseError
    # MVP：允许一切函数；留 ALLOWED_AST_NODES 架子
```

作用域变量集（MVP 全开放）：`context_length`/`loop_count`/`error_flag`/`deliverables`/`messages`/`current_node`/`max_loops`/`next_state`

#### 模块改动清单

| 模块 | 改动 |
|---|---|
| 新增 `triggers.py` | Trigger 数据类、注册表、检查点分发、condition 求值、表达式静态检查、compact() 注入 |
| `config.py` | 加载 triggers.json（全局 + 项目合并）、解析 triggers 段 |
| `parser.py` | 语法糖归一化时注册为内置 trigger；解析 triggers.json 引用 |
| `models.py` | Trigger dataclass、NodeInfo/CompiledSkill 增加 trigger 字段 |
| `executors.py` | pre_llm 检查点（invoke 前，~L245）、on_error 检查点 |
| `nodes.py` | post_node 检查点（~L139 前）调用 trigger 分发 |
| `graph.py`/`runner.py` | 传递 triggers 配置到节点工厂 |

#### 实施顺序

triggers.py 核心 → 检查点埋点 → 语法糖展开 → triggers.json 加载 → 测试（test_triggers.py + config/parser 扩展）

#### 待实施时确认的遗留项

- 触发处理程序是否需显式 `trigger_result`（拦截/放行）注入，还是 MVP 靠"改状态+不跳转"隐式表达（倾向后者）

### 7.7 架构方向结论（openswe 调研后定案）

- **模型选择**：粒度 = skill 级（图级统一），不节点级混用；唯一 pyfunction 接口（开发者自写逻辑），无 DSL 语法糖、无能力映射；兜底链 pyfunction > config.json > 内置默认；LLM 自主选模型 → subagent 类型（未来）
- **介入分层**：轮次级 = init 节点（外部输入/事件/模型重配/轮次级压缩）；操作级 = trigger（本计划书 §7.6）；节点级只接受 DSL 声明（history_window 等），不接受外部介入
- **常驻形态**：图自环（最后节点→init）= 图内多轮迭代（标准做法），但"外部可控多轮会话"必须外部宿主（--json/--resume 协议），内核保持纯函数
- **上下文管理双层**：节点级 = DSL 声明（executor 执行）；轮次级 = init 阶段 compaction；单点 init 做不了节点级策略（结构性边界）
- **subagent 类型**（未来）：openswe 式自由循环作为状态机内的一个节点类型（外层静态跳转约束 + 内层 LLM 自主推进），退出后回跳转表
