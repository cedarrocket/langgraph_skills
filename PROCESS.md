# 项目维护流程（Process）

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
| `scripts/gen_compiler_prompt.py` | spec → COMPILER_PROMPT（方案 A：结构生成+人工措辞） | `python scripts/gen_compiler_prompt.py` | 已启用，随 spec 变更重新生成 |
| `scripts/dump_ir.py` | 现有 parser → IR 初稿（金标准方案 A 生成器） | `python scripts/dump_ir.py <skill.md> [-o out.json]` | 初稿生成，最终契约人工审 |
| `lgskills validate` | 静态校验技能 | `lgskills validate <skill.md>` | 已启用 |
| CI (`ci.yml`) | lint/mypy/test/validate/build | 推送时自动 | 已启用 |
| `pip wheel . --no-deps` | 构建 wheel | `python -m pip wheel . --no-deps -w <dir>` | 已启用 |

> 后续新增的自动化/半自动化流程（例如：金标准示例批量生成、IR 对照工具、
> 编译器 prompt 生成、调试复现脚本）统一登记到本表并附使用说明。

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

## 6. 当前重构单子（进行中）

见会话讨论中的 11 条单子。当前进度：
- [x] 第 1 条 前置：spec 草稿 + gen_docs.py 参考生成器（**文档定稿走 §2.3 手动方式**）
- [ ] 金标准示例生成（方案 A：由 parser 生成初稿 + 人工审）
- [ ] gen_compiler_prompt.py（spec → COMPILER_PROMPT）
- [x] IR 模型定稿 + parser 重写（models.py / parser.py，见下方模块结构）
- [x] executors 可插拔（executors.py：EXECUTOR_REGISTRY + register_executor 扩展点 + 沙箱 config 扩展点）
- [x] tools 每图隔离（tools.py：ToolRegistry 每次 build_graph 新建 + TOOL_FACTORIES 可扩展）
- [x] config 参数化（config.py：Settings.from_env()，LGSKILLS_MODEL/BASE_URL/TEMPERATURE/STRICT；去掉 run_skill 全局 env 副作用）
- [x] CLI 统一（唯一入口 cli.py；删 compiler.py 底部 __main__ 块；lgskills / python -m langgraph_skills 双入口已验证）
- [x] **interpreter.py 拆分**：-> nodes.py（节点工厂+路由器）/ graph.py（图构建）/ runner.py（运行时+CLI），循环依赖靠参数注入打破，interpreter.py 已删除
- [x] 文档生成收尾（README 更新为新模块结构 + spec/生成管线 + 架构分区）
- [x] 测试补齐（48 个测试：config/parser/tools/executors/router/graph/nodes 全覆盖，覆盖率 59%，核心 parser/models/config 87-93%）
- [x] 冗余清理：删除重复文件（test_skills/assistant_compiled_lg.md / code_reviewer_draft.md / compiler_skill.md）；mypy 移入 dev+agents extra；同步 README/requirements/gen_docs 引用
- [ ] 首次提交（用户明确暂不提交，等待后续重构工作）
