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
- 2. subagent 并行执行/管理——**静态 fan-out/fan-in 已实现**（`Parallel ==> A, B, C` 语法 + 自动 join 合并，见 §7.8）；动态并行（Send API 按运行时任务数分发）与 subagent 类型未做，临时可用 `type: script`+线程/子进程绕。
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

### 7.6.1 上下文压缩流程设计（部分实施，其余待实施）

> 目标：解决"零继承 message history"导致的**下游失明**与**死数据累积**问题。
> 核心机制：pre_node 检查点拦截超长上下文 → 跳转压缩子图 → 压缩后回本节点重跑。
> **实现状态：全部决策已实现**（parser/executors/nodes/spec/两个 compiler 同步，134 测试全绿，真实 key 端到端验证通过）。

#### 设计决策

| # | 决策 | 状态 |
|---|---|---|
| 1 | **消息继承 = `==>` 语法**：列表 `- Default ==> Target`、表格 Next Node 单元格 `==> Target` → 目标节点继承源节点消息历史（游标不重置）；默认 `->` 不继承（现状零继承） | ✅ **已实现** |
| 2 | **pre_node 检查点**：节点开头（executor 之前）检查上下文；`max_context_length` 元数据声明阈值，超限时**提前 return** 跳过本节点（超长报文不构造、不传 LLM），跳转到继承边（`==>`）指向的子图 | ✅ **已实现** |
| 3 | **信号机制 = 方案 A（提前 return）**：`node_function` 在 executor 之前 `return {"next_state": 子图节点名}`，router 自动跳转；不用 Python 异常（B） | ✅ **已实现** |
| 4 | ~~transition 表格 `if:` 前缀~~（确认从未实现，**不引入**） | ❌ 取消 |
| 5 | **子图机制 = 通用能力**（非专用 CompactionNode）：压缩是子图的一个应用。子图 = `# [SubGraph]` 声明（形态 A：内部 `## [Node]` 节点列表；src 简写：`- **src**: path` 加载外部 skill），允许递归嵌套 | ✅ **已实现** |
| 6 | **loop 计数语义**：pre_node 触发跳转**不计 loop**（提前 return 分支回传原 loop_count）；子图内部节点独立计数（run_skill 从 0 开始），不污染父图 | ✅ **已实现** |
| 7 | **子图调用继承**：跳转子图的边 `->`（不继承）→ **warning**（不强制）；`==>`（继承+合并）正常 | ✅ **已实现** |
| 8 | **覆盖语法 `==> X <==`**：调用子图时输出（messages）整体覆盖父图 messages（压缩等替换场景）；不带 `<==` 时合并回父图（追加新增，按 ID 去重） | ✅ **已实现** |

#### 机制图

```
[N 节点]
  │
  ├─ pre_node 检查点（新，executor 之前）
  │    ├─ 遍历 transitions：找 if: 前缀行，程序化求值
  │    │    ├─ context_length > 5000 满足
  │    │    │    └─ return {"next_state": "CompactionNode"}  ← 提前 return，报文不传，不计 loop
  │    │    └─ 不满足 → 继续
  │    └─ （无 if: 行）→ 直接继续
  │
  ├─ executor 执行（LLM，可见 = payload + 本节点全部消息）
  │
  └─ return {"next_state": 正常跳转}（计 loop）

[CompactionNode 子图] ← router 跳来
  ├─ 内部多节点：摘要 → compact → 重组
  └─ 子图结束 → 父图边回 [N]（重跑，此时已压缩）
```

#### transition 语法（决策 1 已实现）

```markdown
# [Node] Analyze
## [Transitions]
| Condition | Next Node | Require Approval | Feedback |
| done | ==> Fix | no | 带上分析结论（继承 Analyze 的消息历史） |
| unclear | Reask | no | 重新问（不继承） |

# 列表形式
- Default ==> Fix          # 继承消息历史
- Default -> Reask          # 不继承（默认）
```

- `==>`：目标节点继承源节点消息历史（游标不重置，从源节点起点继续看）；默认 `->` 不继承（零继承，现状）
- 已实现：parser（表格/列表）、executors（游标逻辑 + _inherit_history 标记）、spec/两个 compiler 同步

#### 关键事实（LangGraph 实测）

- **子图 = 完整图，可多节点**（3 节点子图验证通过）
- **recursion_limit 把子图作为"一步"计入**（父图 3 步 + 子图 2 节点，limit=3 跑完父图）
- **子图内部节点与父图共享 state**（子图内能看到父的 loop_count）——**须防止子图内部 +1 污染父图计数**
- 我们自建 `loop_count`（nodes.py:60 节点函数开头 +1）；子图节点执行时父图 +1，但子图内部节点不能再 +1

#### 待实施时的实现要点

1. `parser.py`：Transition.condition 支持 `if:` 前缀（存原始文本，pre_node 判断前缀）
2. `nodes.py`：新增 `_run_pre_node_checkpoint`（executor 之前），找 `if:` 行求值；满足 → 提前 return（next_state=目标，不计 loop）
3. `nodes.py`：loop 计数调整——pre_node 触发跳转不计；子图内部节点不累加父图 loop_count（需在子图执行时保护）
4. `graph.py`：支持子图节点（`type: skill` 编译为 LangGraph 子图，`add_node("x", subgraph)`）——**这是独立重构项，见下方**
5. 继承语义（选项 A）：节点可见 = payload + 本节点全部消息（现状 executors.py:227-236 的 `start_msg_index` 逻辑微调）

#### 相关独立重构项（另立项）

- **`# [SubGraph]` 升级为真子图**（✅ 已实现，commit 见 git log）：`# [SubGraph]` 声明编译为 `add_node(子图)`（LangGraph 原生子图）。子图状态接口 = schema 字段声明制（共享字段自动进出）；messages 用自定义 reducer（`ReplaceMessages` 支持整体覆盖）。覆盖语义：子图内部写 `deliverables["_child_messages"]`（压缩结果），父图子图节点后的 `_sub_after_<name>` 后处理节点按 `_replace_messages` 标志整体替换。支持递归嵌套（内部 `## [SubGraph]` + `### [Node]`）。`type: skill`（run_skill 模拟）保留为兼容路径。
- **LLM 摘要（llm_summarize）不再需要**（用户确认）：压缩场景由子图机制承担（子图内用 `type: llm` 节点做摘要即可），不引入专用 handler。

### 7.7 架构方向结论（openswe 调研后定案）

- **模型选择**：粒度 = skill 级（图级统一），不节点级混用；唯一 pyfunction 接口（开发者自写逻辑），无 DSL 语法糖、无能力映射；兜底链 pyfunction > config.json > 内置默认；LLM 自主选模型 → subagent 类型（未来）
- **介入分层**：轮次级 = init 节点（外部输入/事件/模型重配/轮次级压缩）；操作级 = trigger（本计划书 §7.6）；节点级只接受 DSL 声明（history_window 等），不接受外部介入
- **常驻形态**：图自环（最后节点→init）= 图内多轮迭代（标准做法），但"外部可控多轮会话"必须外部宿主（--json/--resume 协议），内核保持纯函数
- **上下文管理双层**：节点级 = DSL 声明（executor 执行）；轮次级 = init 阶段 compaction；单点 init 做不了节点级策略（结构性边界）
- **subagent 类型**（未来）：openswe 式自由循环作为状态机内的一个节点类型（外层静态跳转约束 + 内层 LLM 自主推进），退出后回跳转表

### 7.8 静态 fan-out / fan-in（并行分支 + 收敛合并，✅ 已实现）

**定位**：工作流型并行原语（LangGraph 简易版的核心功能之一）。它是动态并行（Send API）的地基——Send = "运行时才知道分支数"的 fan-out。

**语法**（零新前缀，复用现有 transition 语法）：
```markdown
## [Transitions]
- Parallel ==> 检索A, 检索B, 检索C    # 列表：逗号/分号分隔
| Condition | Next Node |
| --- | --- |
| Parallel | 检索A, 检索B |     # 表格同样支持
```
- 解析为多个 `parallel=True` 的 Transition
- 代码节点程序化扇出：`transition_to(["A", "B"], payload)`
- **fan-in 零语法**：多分支共同目标自动成为 join 节点（LangGraph 调度器等所有入边来源——含链式分支链尾——完成后触发一次）

**合并规则**（fan-in 时节点收到的 state）：
| channel | reducer | 行为 |
|---|---|---|
| `messages` | add_messages | 追加合并，按 ID 去重 |
| `deliverables` | merge_dicts（新增） | 字段级合并，各分支写独立 key |
| current_node/next_state/loop_count/max_loops | last_wins（新增） | 多分支写同 key 不冲突（后到覆盖） |

**验证**（实验结论）：
- join 节点只触发一次，且收到全部分支合并产出
- 提前 END 的分支 → join 永不触发（死等）→ **validator 强制检查**：fan-out 分支必须收敛到共同 join
- 链式分支（A→B→C 与 A→D 并行）join 等待链尾 C、D
- 并行真实生效（两个 0.3s 分支耗时 ~0.3s 而非 0.6s）

**实现**：models.py（Transition.parallel + merge_dicts/last_wins reducer）、parser.py（Parallel 前缀 + 拆分）、nodes.py（next_state 列表 + generic_router 原样返回）、parser.py validator（收敛检查）。测试 test_parallel.py（7 个）。

**未来**：动态并行（Send API，map-reduce 场景如"每段一个 worker"）在此机制上扩展；subagent 类型（§7.7）依赖它。

### 7.9 节点钩子：NodeStart / NodeEnd（✅ 已实现）

**定位**：把 LangGraph 的"节点函数体自管输入"DSL 化。**entry/pre_node ≈ turn_start**（节点入口），**NodeEnd/post_node ≈ turn_end**（节点出口）。图级介入点（Start/End 区块）未来补。

**语法**：
```markdown
## [NodeStart]
- **context**: all                  # all(缺省,全部历史) | previous_payload | executor
- **on**: context_length_exceeded(5000) :=> compact   # 内置谓词/ pyfunction:/ trigger:
## [Transitions]                   # 唯一跳出出口，signal 名匹配 Condition 列
| Condition | Next Node |
| compact   | Compact   |
## [NodeEnd]
- **on**: loop_count_exceeded(3) :=> give_up
```

**语义**：
- `on: <检测器> :=> <signal>`：命中抛 signal → 进 Transitions 按 signal 路由。NodeStart 命中 = 跳过本次 LLM 执行；NodeEnd 命中 = 覆盖 next_state
- signal 名必须与 Transitions 表格 Condition 列一致（validator 检查，无匹配 warning 忽略）
- 检测器三形态：内置谓词（context_length_exceeded/loop_count_exceeded/error_flag/tool_failed，白名单可扩展）/ `pyfunction: path` / `trigger: rules.json`
- **context 三模式**：all（缺省全量）/ previous_payload（只继承上一节点最终 payload）/ executor（执行 NodeStart 代码块产出 ctx_messages，空产出报错）
- **NodeEnd 可读不可写上下文**：不提供 context 字段（解析报错）；要动上下文只能抛 signal 调子图
- 谓词一律函数糖（不做表达式解析，`>`/`<` 不引入；所有解析交给外部 Python）

**设计结论**：
- 上下文是"输入"，NodeStart 管；输出/跳转 NodeEnd 管，职责分离
- trigger 检查点（pre_llm/post_node）被 NodeStart/NodeEnd 的 on: 吸收；trigger.json 保留为 `trigger:` 引用
- `max_context_length` 保留为 `context_length_exceeded(n)` 的向后兼容糖（未来可能废除）

**实现**：models.py（NodeHook/OnCondition）、parser.py（区块解析 + 谓词白名单）、executors.py（context 三模式构造）、nodes.py（on 求值 + signal 对接）。测试 test_node_hooks.py（12 个）。

### 7.10 NodeEnd executor signal() + 工具边界 hooks（✅ 已实现）

**NodeEnd executor signal() API**：NodeEnd 的 Python 代码块内可调用 `signal("name")` 抛 condition → 进 Transitions 按 signal 覆盖路由（与 on: 相同对接）。与 on: 的区别：executor 里不用提前声明会抛哪些 signal。NodeStart/NodeEnd 共用一个通用 executor 执行器（`_exec_hook_executor`，注入 state/messages/deliverables/spans/get_payload/transition_to/signal/HumanMessage 等）。

**工具边界 hooks（hooks.json）**：对标 Claude Code PreToolUse/PostToolUse/PostToolUseFailure。
```json
{
  "hooks": {
    "pre_tool":   [{ "matcher": "*", "handler": "pre_tool.py" }],
    "post_tool":  [{ "matcher": "read_file", "handler": "audit.py" }],
    "post_tool_failure": [{ "matcher": "*", "handler": "fallback.py" }]
  }
}
```
- 独立 `hooks.json`（全局 `~/.config/langgraph_skills/` + 项目根，规则拼接非覆盖）
- matcher：工具名 glob（`read_*` / `mcp__*`）
- handler 注入：`tool_name` / `tool_args` / `tool_result` + deliverables/messages/get_payload/compact/current_node
- 执行点：graph.py ToolNode 包装器内（pre_tool 调用前；post_tool/post_tool_failure 调用后）

**实现**：hooks.py（ToolHooks/ToolHookRule/load_hooks/fire_tool_hooks）、graph.py（ToolNode 包装器触发 + matcher 匹配）、executors.py（`_exec_hook_executor`）、nodes.py（NodeEnd signal 对接）、triggers.py（run_handler 注入 scope 全键）。测试 test_hooks.py（6 个）+ test_node_hooks.py（+2）。

### 7.11 工具调用子图化（方案验证，✅ 可行性确认）

**背景**：实操（file-assistant agent）暴露"自由式 ReAct 模型驱动不稳"（LLM 跳步/误判工具）。结论：不靠 must_call 硬编码强制（已撤销），改为**工具调用一律走子图**。

**方案**（experiments/tool_subgraph/）：
```
LLM 节点（决策）→ 输出结构化指令 JSON 到 payload
  → `==> ToolExec <==` 跳工具子图
  → 子图内 code/script 节点读指令、执行（带安全边界）、写 tool_result
  → `==> <==` 回传父图 → 后续节点
```

**验证结论**（真实 key）：
- ✅ LLM 自主决策出指令（`{"tool": "read_file", "path": ...}`）→ 子图执行 → 回传结果（读到文件内容）
- ✅ 安全边界：子图内 `if not path.startswith(allow_root)` 拒绝超界路径（`/etc/passwd` 被拦截，返回 `Error: 安全边界拒绝`）
- ✅ 执行确定性：工具执行是子图内 Python（可审计、可加权限/边界），不依赖 LLM 直接调工具

**架构意义**：这解决了"模型驱动不稳"——LLM 只在"决策点"出现（输出指令），执行由子图内确定代码完成。安全边界/权限在子图内声明式表达，比 ToolNode 全局权限更细粒度（每个工具子图自管）。

**关键设计问题（待后续）**：
- LLM 输出指令的格式约定（目前是自由 JSON 到 payload；未来可定义 `## [Output JSON]` schema 强制）
- 工具子图的复用/注册（一个工具 = 一个可复用子图，可能挂到 `# [Tools]` 级）
- 与现有 ToolNode ReAct 的关系（子图化作为"复杂/需安全边界工具"的路径，保留传统 ReAct 作为快捷路径）

**当前 streaming**（§7.10 后补）：`on_token` 回调逐 token 流式输出（方案 A，闭包回调）；`_invoke_llm` stream + 聚合（tool_calls 名修正）。测试 test_streaming.py。

**子图方案小型 pi agent 验证（experiments/pi_like_subgraph/pi_agent.md）**：
- 多轮交互 + LLM 决策输出工具指令 JSON → `NodeEnd executor` 检测 `{` 前缀抛 `tool_call` signal → 跳工具子图 → 子图内嵌 Python 执行（安全边界 `/tmp/opencode/pi_work`）→ 回传 → Report 汇报 → 回 Agent 下一轮
- 实测（真实 key）：读取 ✓（文件内容回传）、写入 ✓（note.txt 真实创建 `wrote 5 chars`）、追加 ✓（`appended 6 chars`）、越界拒绝 ✓（LLM 决策层拒绝 + 子图边界双保险）
- **结论：子图方案下小型 pi agent 类似物可行**。LLM 只在决策点（输出指令），执行确定性 + 安全边界，解决了自由 ReAct 的模型驱动不稳。
