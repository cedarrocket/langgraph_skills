# LangGraph Skill 声明式状态机语法指南

> 本文档由 `scripts/gen_docs.py` 依据 `spec/dsl_spec.yaml` 自动生成，请勿手动编辑。修改语法请先改 spec 再重新生成。

---

## 1. 文档结构

* **Shebang**（可选）：首行可选 #!/usr/bin/env lgskills，解析时跳过。
* **全局文本**（可选）：第一个顶层 section 之前的文本，作为全局系统提示词。
* **顶层 section**：按 `# [SectionType] OptionalArg` 模式切块，顶层 section 按此模式切块；每块是独立声明区域。

## 2. 语法规范

### 2.1 引擎配置 (# [Config])

引擎运行时参数，由解释器消费：
```markdown
# [Config]

- **max_loops**: 10

```

 * `max_loops`，（默认 `10`），整张图的全局执行预算上限



### 2.2 进程 I/O 声明 (# [IO])

声明 skill 的输入输出工具；解析器据此生成保留参数 input_path / output_path：
```markdown
# [IO]
- **reader**: txt_reader
- **writer**: txt_writer
```

 * `reader`，读入工具。存在时自动注册保留参数 input_path

 * `writer`，写出工具。存在时自动注册保留参数 output_path

* **保留参数 `input_path`**：读文件内容 -> deliverables['payload']；'-' 或缺省走 stdin。

* **保留参数 `output_path`**：最终 payload 写出；'-' 或缺省走 stdout。



### 2.3 工具声明 (# [Tools])

声明 skill 可用的工具（脚本或 API）：
```markdown
# [Tools]
- **mock_tool**:
  - **type**: script
  - **src**: path/to/script.py
```

 * `type`，可选值：`script` / `api`，（默认 `script`），工具种类

 * `src`，（当 type == script 时必填），脚本文件路径

 * `url`，（当 type == api 时必填），API 地址

 * `method`，可选值：`GET` / `POST`，（默认 `GET`），HTTP 方法（仅 api）

 * `description`，（默认 ``），工具描述



### 2.4 状态节点 (# [State] StateName)

一个状态节点。元数据键：

 * `type`，可选值：`llm` / `code` / `script` / `skill`，（默认 `llm`），节点类型，决定执行器

 * `tools`，（默认 `[]`），绑定到该节点的工具

 * `src`，（当 type in (script, skill) 时必填），脚本或嵌套 skill 的路径

 * `interactive`，（默认 `False`），是否交互式（与用户对话）

 * `is_final`，（默认 `False`），是否为终态

 * `history_window`，该节点可见的对话历史轮数；null 表示不限制

 * `max_loops`，该节点独立执行上限；null 表示继承全局 max_loops



**子区块 `## [Transitions]`**：状态跳转规则；非 final 状态必须定义，否则触发隐式顺序 fallback

**输出约束**：`## [Output JSON]` / `## [Output Schema]` / `## [Output]`——输出 JSON Schema 约束；内容为 JSON 文本（可带 ```json 围栏）



## 3. 状态转移定义

### 3.1 顺序自动跳转（缺省模式）

非 final 且无 transitions 时，自动连接声明顺序的下一个状态。

### 3.2 列表式跳转

```markdown
## [Transitions]
- Default -> TargetState
```

```markdown
## [Transitions]
- If `need_revision` -> Draft (Feedback: "The article is too short. Please expand.")
- If `approved` -> Publish [Require Approval]
```

* `[Require Approval]` / `(Require Approval)`：跳转前需人工审批。

* `(Feedback: "...")`：跳转时回传给目标状态的反馈文本。


### 3.3 Markdown 表格转移（推荐）

```markdown
## [Transitions]
| Condition | Next State | Require Approval | Feedback |
| :--- | :--- | :--- | :--- |
| Too short | Draft | no | Please expand. |
| Acceptable | Publish | yes | |
```

* `Condition`：触发跳转的判断条件（仅作为对 LLM 的提示，不程序化求值）。

* `Next State`：目标状态名。

* `Require Approval`：`yes`/`true` 开启人工审批门。

* `Feedback`：回传给目标状态的反馈。


### 3.4 人工审批门

跳转声明 `Require Approval` 后，解释器在跳转前交互式挂起：显示 payload，等待输入 `Approve? (y / n / [enter feedback to reject and revise]): `。`y` 放行；`n` 或反馈则拒绝并返回源节点自愈。


## 4. 语义规则

* **未知 section**：warning（可通过配置关闭）——源首先是 markdown，允许自然语言混入
* **重复状态名**：error——结构性冲突，破坏图语义
* **条件跳转**：条件仅作为对 LLM 的提示，由 LLM 决定 next_state（不程序化求值）
* **循环预算**：来自 # [Config] max_loops（默认 10）；节点可覆盖（来自状态元数据 max_loops；null 时继承全局）；超限后 全局或节点任一超限 -> 强制 END 并警告。归一化在 在语义分析阶段统一归一化为整数，不留运行时。
* **`llm` 状态**：LLM 节点：构造 prompt、绑定工具、可交互
* **`code` 状态**：Python 代码节点：exec 状态体中的代码
* **`script` 状态**：脚本节点：exec src 指向的文件
* **`skill` 状态**：嵌套 skill 节点：运行子 skill，payload 作为输入
* **I/O 契约**：input_path 读入内容 -> deliverables['payload']（唯一显式规则）；最终 payload 写出（stdout 或 writer 工具），其余为日志走 stderr。

## 5. Python 代码节点 SDK

`type="code"`（内嵌代码）与 `type="script"`（外挂脚本）节点直接执行 Python，注入以下通信 API：

* **`get_payload()`**：获取上一个节点传来的 `payload` 文本。
* **`transition_to(next_state_name, payload_data)`**：触发跳转并传递数据。
* **`deliverables`**：全局数据字典（可读写）。
* **`messages`**：对话历史列表。

```markdown
# [State] Check
- **type**: code

```python
guess = get_payload()
if int(guess) == 42:
    transition_to('Win', 'Correct')
else:
    transition_to('Guess', 'Too high' if int(guess) > 42 else 'Too low')
```
```

## 6. 命令行集成

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
* **`--input_path`**（别名 `--input`/`-i`）：读文件内容 -> deliverables['payload']；'-' 或缺省走 stdin。
* **`--output_path`**（别名 `--output`/`-o`）：最终 payload 写出；'-' 或缺省走 stdout。
* 帮助菜单：`lgskills run <skill> --help`。

### 6.4 管道输入
非 TTY 环境下自动读取 `stdin` 并注入 `deliverables["stdin"]`。

### 6.5 退出状态码
* `0`：成功。
* `1`：异常/崩溃。
* `2`：参数校验失败。
* `3`：达到 `max_loops` 且未到终态。

## 7. 工作流

1. **草稿编写**：人类用声明式语法编写 `.md`。
2. **编译（可选）**：用 LLM 编译器将松散草稿标准化为符合本 spec 的 Markdown。
3. **校验**：`lgskills validate <skill.md>` 静态校验。
4. **运行**：`lgskills run <skill.md> [input]` 解析并驱动 LangGraph 执行。
