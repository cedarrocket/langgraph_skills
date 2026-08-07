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

### 7.2 配置与外部语法（战略方向）

- 允许 config / tool 设置从 markdown 解耦到 YAML/JSON 等标准格式（当前 `# [Config]`/`# [IO]`/`# [Tools]` 用 markdown 列表，单机 MVP 够用）。
- config 路径支持：1) 默认文件路径，允许字头协议（如 `redis://127.0.0.1:6379/agent_config_key`）；2) 系统变量展开（如 `${AGENT_CONFIG_URI}`）。
- **触发时机**：出现真实多 agent / 分布式配置需求时。当前 markdown config 不阻塞。

### 7.3 其它（低优先 / 长期搁置）

- **langgraph 特性微调**（如 reducer、checkpoint 配置）：power-user 功能，普通用户用不到，长期搁置。
- **userid 选项**：功能极小（config 字段 + 注入 deliverables），但价值依赖 §7.2 的配置加载机制，随 §7.2 做。
- **图可视化依赖**（pygraphviz 等）：见 §3 图可视化说明，不加入项目依赖。

### 7.4 性能结论（已定，防将来纠结）

Python 不构成瓶颈，保持 Python 不迁移（详见 §1 核心原则）。优化方向是解析/构图缓存、asyncio 并发、消息历史裁剪，而非换语言。
