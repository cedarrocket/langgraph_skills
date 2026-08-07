"""从 spec/dsl_spec.yaml 生成 skill_syntax_guide.md 的**参考文档**。

用法:
    python scripts/gen_docs.py              # 写入 skill_syntax_guide.md（参考版）
    python scripts/gen_docs.py --stdout     # 打印到 stdout（便于 diff）

定位:
    本脚本是**参考文档生成器**，产出骨架供人工定稿参考，不是最终文档。
    最终文档由人手动写/润色，语法事实必须与 spec 一致（见 PROCESS.md §2.3）。
    修改 DSL 语法后：先回写 dsl_spec.yaml -> 重新运行本脚本 -> 手动定稿正式文档。
"""

import sys
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "spec" / "dsl_spec.yaml"
OUT_PATH = ROOT / "skill_syntax_guide.md"


def load_spec() -> Dict[str, Any]:
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_meta(spec: Dict[str, Any]) -> str:
    return (
        "# LangGraph Skill 声明式状态机语法指南\n\n"
        "> 本文档由 `scripts/gen_docs.py` 依据 `spec/dsl_spec.yaml` 自动生成，"
        "请勿手动编辑。修改语法请先改 spec 再重新生成。\n\n"
        "---\n"
    )


def render_document_structure(spec: Dict[str, Any]) -> str:
    doc = spec["document"]
    lines = ["## 1. 文档结构\n"]
    if doc["shebang"]["optional"]:
        lines.append(f"* **Shebang**（可选）：{doc['shebang']['description']}。")
    lines.append(f"* **全局文本**（可选）：{doc['global_text']['description']}。")
    lines.append(
        f"* **顶层 section**：按 `{doc['top_level_section']['syntax']}` 模式切块，"
        f"{doc['top_level_section']['description']}。\n"
    )
    return "\n".join(lines)


def _fmt_field(name: str, field: Dict[str, Any]) -> str:
    default = field.get("default")
    default_str = f"（默认 `{default}`）" if default is not None else ""
    req = f"（当 {field.get('required_when')} 时必填）" if field.get("required_when") else ""
    enum = f"可选值：`{'` / `'.join(field['enum'])}`" if field.get("enum") else ""
    desc = field.get("description", "")
    bits = [p for p in [f"`{name}`", enum, req, default_str, desc] if p]
    return " * " + "，".join(bits)


def render_sections(spec: Dict[str, Any]) -> str:
    s = spec["sections"]
    out = ["## 2. 语法规范\n"]

    # 2.1 Config
    cfg = s["config"]
    out.append(f"### 2.1 引擎配置 ({cfg['heading']})\n")
    out.append(f"{cfg['description']}：\n```markdown\n{cfg['heading']}\n")
    out.append("".join(f"- **{k}**: {v.get('default', '')}\n" for k, v in cfg["fields"].items()))
    out.append("```\n")
    for name, field in cfg["fields"].items():
        out.append(_fmt_field(name, field) + "\n")
    out.append("\n")

    # 2.2 IO
    io = s["io"]
    out.append(f"### 2.2 进程 I/O 声明 ({io['heading']})\n")
    out.append(f"{io['description']}：\n```markdown\n{io['heading']}\n- **reader**: txt_reader\n- **writer**: txt_writer\n```\n")
    for name, field in io["fields"].items():
        out.append(_fmt_field(name, field) + "\n")
    for opt_name, opt in io["reserved_options"].items():
        out.append(f"* **保留参数 `{opt_name}`**：{opt['description']}。\n")
    out.append("\n")

    # 2.3 Tools
    tl = s["tools"]
    out.append(f"### 2.3 工具声明 ({tl['heading']})\n")
    out.append(f"{tl['description']}：\n```markdown\n# [Tools]\n- **mock_tool**:\n  - **type**: script\n  - **src**: path/to/script.py\n```\n")
    for name, field in tl["fields"].items():
        out.append(_fmt_field(name, field) + "\n")
    out.append("\n")

    # 2.4 State
    st = s["state"]
    out.append(f"### 2.4 状态节点 ({st['heading']})\n")
    out.append(f"{st['description']}。元数据键：\n")
    for name, field in st["metadata"].items():
        out.append(_fmt_field(name, field) + "\n")
    out.append("\n")
    out.append(f"**子区块 `{st['sub_sections']['transitions']['heading']}`**："
               f"{st['sub_sections']['transitions']['description']}\n")
    out.append(f"**输出约束**：`{'` / `'.join(st['sub_sections']['output']['headings'])}`——"
               f"{st['sub_sections']['output']['description']}\n")
    out.append("\n")
    return "\n".join(out)


def render_semantics(spec: Dict[str, Any]) -> str:
    sem = spec["semantics"]
    out = ["## 3. 状态转移定义\n"]

    # 3.1 顺序自动跳转
    nf = sem["non_final_without_transitions"]
    out.append("### 3.1 顺序自动跳转（缺省模式）\n")
    out.append(f"{nf['description']}。\n")

    # 3.2 列表式跳转
    out.append("### 3.2 列表式跳转\n")
    out.append("```markdown\n## [Transitions]\n- Default -> TargetState\n```\n")
    out.append("```markdown\n## [Transitions]\n- If `need_revision` -> Draft (Feedback: \"The article is too short. Please expand.\")\n- If `approved` -> Publish [Require Approval]\n```\n")
    out.append(f"* `[Require Approval]` / `(Require Approval)`：{sem['transition']['require_approval']['description']}。\n")
    out.append(f"* `(Feedback: \"...\")`：{sem['transition']['feedback']['description']}。\n\n")

    # 3.3 表格转移
    out.append("### 3.3 Markdown 表格转移（推荐）\n")
    cols = " | ".join(spec["sections"]["state"]["sub_sections"]["transitions"]["forms"]["table"]["columns"])
    out.append(f"```markdown\n## [Transitions]\n| {cols} |\n| :--- | :--- | :--- | :--- |\n| Too short | Draft | no | Please expand. |\n| Acceptable | Publish | yes | |\n```\n")
    out.append("* `Condition`：触发跳转的判断条件（仅作为对 LLM 的提示，不程序化求值）。\n")
    out.append("* `Next Node`：目标状态名。\n")
    out.append("* `Require Approval`：`yes`/`true` 开启人工审批门。\n")
    out.append("* `Feedback`：回传给目标状态的反馈。\n\n")

    # 3.4 审批门
    out.append("### 3.4 人工审批门\n")
    out.append("跳转声明 `Require Approval` 后，解释器在跳转前交互式挂起：显示 payload，等待输入 `Approve? (y / n / [enter feedback to reject and revise]): `。`y` 放行；`n` 或反馈则拒绝并返回源节点自愈。\n\n")

    out.append("## 4. 语义规则\n")
    out.append(f"* **未知 section**：{sem['unknown_section']['policy']}"
               f"{'（可通过配置关闭）' if sem['unknown_section']['closable'] else ''}——"
               f"{sem['unknown_section']['reason']}")
    out.append(f"* **重复节点名**：{sem['duplicate_node_name']['policy']}——"
               f"{sem['duplicate_node_name']['reason']}")
    tr = sem["transition"]
    out.append(f"* **条件跳转**：{tr['condition']['semantics']}")
    ml = sem["max_loops"]
    out.append(f"* **循环预算**：{ml['global']}；节点可覆盖（{ml['node']}）；"
               f"超限后 {ml['enforcement']}。归一化在 {ml['normalization']}。")
    for stype, desc in sem["node_executor_map"].items():
        out.append(f"* **`{stype}` 状态**：{desc}")
    ic = sem["io_contract"]
    out.append(f"* **I/O 契约**：{ic['input_rule']}；{ic['output_rule']}。")
    return "\n".join(out) + "\n"


def render_sdk() -> str:
    return """## 5. Python 代码节点 SDK

`type="code"`（内嵌代码）与 `type="script"`（外挂脚本）节点直接执行 Python，注入以下通信 API：

* **`get_payload()`**：获取上一个节点传来的 `payload` 文本。
* **`transition_to(next_state_name, payload_data)`**：触发跳转并传递数据。
* **`deliverables`**：全局数据字典（可读写）。
* **`messages`**：对话历史列表。

```markdown
# [Node] Check
- **type**: code

```python
guess = get_payload()
if int(guess) == 42:
    transition_to('Win', 'Correct')
else:
    transition_to('Guess', 'Too high' if int(guess) > 42 else 'Too low')
```
```
"""


def render_cli(spec: Dict[str, Any]) -> str:
    io = spec["sections"]["io"]
    ip = io["reserved_options"]["input_path"]
    op = io["reserved_options"]["output_path"]
    return f"""## 6. 命令行集成

### 6.1 Shebang 直接执行
文件首行 `#!/usr/bin/env lgskills` + `chmod +x` 后可直接运行：
```markdown
#!/usr/bin/env lgskills

# [IO]
- **reader**: txt_reader
- **writer**: txt_writer
```
```bash
./bootstrap_compiled.md --input_path draft.md --output_path compiled.md
```

### 6.2 标准流分离
日志与交互提示走 **stderr**；仅最终 `payload` 走 **stdout**，便于管道收集。

### 6.3 保留参数
* **`--input_path`**（别名 `--input`/`-i`）：{ip['description']}。
* **`--output_path`**（别名 `--output`/`-o`）：{op['description']}。
* 帮助菜单：`lgskills run <skill> --help`。

### 6.4 管道输入
非 TTY 环境下自动读取 `stdin` 并注入 `deliverables["stdin"]`。

### 6.5 退出状态码
* `0`：成功。
* `1`：异常/崩溃。
* `2`：参数校验失败。
* `3`：达到 `max_loops` 且未到终态。
"""


def render_pipeline() -> str:
    return """## 7. 工作流

1. **草稿编写**：人类用声明式语法编写 `.md`。
2. **编译（可选）**：用 LLM 编译器将松散草稿标准化为符合本 spec 的 Markdown。
3. **校验**：`lgskills validate <skill.md>` 静态校验。
4. **运行**：`lgskills run <skill.md> [input]` 解析并驱动 LangGraph 执行。
"""


def main() -> None:
    spec = load_spec()
    sections = [
        render_meta(spec),
        render_document_structure(spec),
        render_sections(spec),
        render_semantics(spec),
        render_sdk(),
        render_cli(spec),
        render_pipeline(),
    ]
    output = "\n".join(sections)
    if "--stdout" in sys.argv:
        sys.stdout.write(output)
    else:
        OUT_PATH.write_text(output, encoding="utf-8")
        print(f"Generated: {OUT_PATH} ({len(output)} bytes)")


if __name__ == "__main__":
    main()
