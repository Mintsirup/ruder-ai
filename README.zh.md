# RUDER-AI

**语言:** [English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · **[简体中文](README.zh.md)**

**RUDER-AI** 是一个角色分离的自主编码代理，旨在将自然语言任务转化为软件工作区中
真实、可验证的改动。

RUDER-AI 将 LLM 推理与确定性 Tool 执行、按角色的权限授予、独立验证以及多语言模型
配置结合起来。

## 环境要求

* Python 3.10 或更高版本
* 本地运行的 [Ollama](https://ollama.com)（默认 `http://127.0.0.1:11434`）
* `tkinter` — Windows/macOS 的 CPython 自带该模块。在 Linux 上如需 GUI，请安装
  `python3-tk`。终端代理和测试套件不使用它。

## 安装

```bash
git clone https://github.com/Mintsirup/ruder-ai.git
cd ruder-ai
python -m pip install -e .
```

这会安装 `ruder-ai` CLI。用下面的方式启动交互式代理：

```bash
ruder-ai start --dir /path/to/your/project --model ruder-ai-ko
```

### 命令

| 命令 | 作用 |
|---|---|
| `ruder-ai start` | 在目标项目上运行交互式代理 |
| `ruder-ai gui` | 打开桌面编辑器 + AI 控制台 |
| `ruder-ai where` | 打印当前实际运行的检出，并在另一份副本代码不同时发出警告 |
| `ruder-ai survey` | 按角色分类并说明项目中的每个文件（不调用 LLM） |
| `ruder-ai bench` | 度量关键路径：scan、index、plan、context、每次编辑的刷新 |
| `ruder-ai config` | 打开设置窗口 |

## 桌面 GUI

RuderAI Studio 是一个 Code-OSS 风格的桌面应用 —— 文件浏览器、多标签编辑器、
AI 控制台和内置终端，完全基于 Python 标准库的 `tkinter` 实现。没有需要安装的
GUI 二进制包，因此除了 `pip install -e .` 之外无需其他步骤即可运行。

```bash
ruder-ai gui      # 打开 RuderAI Studio
ruder-ai config   # 设置窗口：工作区、Ollama URL、模型、temperature
```

说明：

* 每次运行 —— CLI、GUI 对话以及 `.ruder_ai_logs/` 中的 JSONL 执行日志 —— 都会以
  显式的 `End Of Token` 标记结束，因此对话记录总能区分「代理已完成」和「流在中途被
  截断」。在日志中它是一个 `end_of_token` 事件。
* 从子进程捕获的输出（`execute_code`、`execute_shell`、`git`、各验证运行器）会在子进程
  中被强制为 UTF-8。在 Windows 上，被管道连接的进程会用 ANSI 代码页编码 stdout，
  若不做此处理，代理会把自己刚写下的韩文输出读成乱码，进而断定刚写的代码已经损坏。
* `tkinter` 随 Windows 和 macOS 的 CPython 一起分发。部分精简的 Linux 镜像需要
  `python3-tk`；无头环境（服务器、Termux）没有显示器，无法运行 GUI —— 在这类环境中
  请使用 `ruder-ai start`。
* 内置终端连接到**真正的 TTY**：在 Linux/macOS 上是 PTY，在 Windows 上是 Win32
  **ConPTY** 伪控制台，因此 `vim`、`htop` 和各语言 REPL 都能正常工作。如果宿主暴露了
  ConPTY API 却拒绝分配控制台主机（容器、非交互式会话、部分 CI 运行器），Studio 会
  降级为管道 shell 并在终端中说明这一点 —— 普通命令仍可使用，全屏程序则不行。
* `gui` 与 `config` 读写同一个 `.ruder_ai_config.json`，与终端代理一致，因此模型或
  宿主地址的变更会同时生效于所有入口。
* 编辑器为 Python、C 系语言（JS/TS、C#、Java、C/C++、Go、Rust、Kotlin、Swift、PHP、
  Lua）、JSON、YAML、shell、CSS、Markdown、XML/HTML 和 Ruby 提供语法高亮。高亮做了
  防抖处理，缓冲区过大或单行过长时直接以无高亮方式渲染，以免卡顿。
* 查找/替换：`Ctrl+F` / `Ctrl+H`，`F3` 与 `Shift+F3` 重复查找，`Esc` 关闭面板或取消
  选区。选项包括区分大小写、全词匹配、正则表达式，以及在精确查找无结果时给出近似
  候选的「오타 허용」容错回退。无效模式会被报告而不会抛出异常，状态行显示 `Ln/Col`。
* 全词匹配采用 Unicode 感知实现而非正则 `\b`，因此对韩文/CJK 以及由标点组成的
  查找串（例如注释中的 `//`）都能正确工作。正则搜索跨多行进行，所以 `^import` 能匹配
  所有以 `import` 开头的行。

## 开发

在仓库根目录运行测试套件：

```bash
python -m pip install pytest pytest-asyncio
python -m pytest
```

`tests/conftest.py` 默认开启 `RUDER_AI_BENCHMARK_FIXTURE_MODE`，该模式选择编排器的
确定性基准测试答案而不调用真实模型。你可以自行设置该变量来覆盖它（例如设置
`RUDER_AI_BENCHMARK_FIXTURE_MODE=0` 以走真实 LLM 代码路径）。

## 翻译

本文档还提供英文、韩文和日文版本。各版本与本文件逐节对应；如有出入，以英文文件为准。

| 语言 | 文件 |
|---|---|
| English | [README.md](README.md) |
| 한국어 | [README.ko.md](README.ko.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| 简体中文 | [README.zh.md](README.zh.md) |

## 特性

* 自主编码工作流
* 角色分离的代理流水线
* 确定性的 Tool 权限强制
* 轮次意图门控 —— 一句问候不会触达文件系统
* 带覆盖率报告的完整项目概览与综述
* 对项目的独立验证
* 基于真实文件与 diff 的主张核验
* 感知项目的代码生成
* 重新规划与恢复
* 网页搜索与信息检索
* 记录最终任务结果的记忆机制
* 多语言模型配置
* 支持韩文、中文、英文、日文
* 内置基准测试（`ruder-ai bench`），覆盖每个任务都要付出的关键路径

---

## v7.5.9 代理流水线

```text
Explorer → Planner → Coder → Tester → Reviewer → Memory
                 \_______________________________/
                           Orchestrator
```

RUDER-AI 将编码工作流拆分为各具专长的阶段。

### Explorer

只读的项目探查。

* 检查项目结构
* 搜索文件
* 查看符号与引用
* 识别项目语言与框架
* 不修改文件

### Planner

制定实现计划。

* 分析请求的任务
* 确定所需的改动
* 识别相关文件与组件
* 在实现之前产出结构化计划

### Coder

应用计划好的改动。

* 创建和修改文件
* 应用补丁
* 执行获准的开发 Tool
* 实现计划中的方案

验证被刻意推迟到 Tester 阶段。

### Tester

独立验证实现结果。

* 运行 `verify_project`
* 检查项目状态
* 确认实现是否真的能工作
* 不修改文件

因此，Coder 并不是判定自身工作是否成功的最终权威。

### Reviewer

对照现实审查结果。

* 检查被修改的文件
* 检查 diff
* 核验实现主张
* 找出报告改动与实际改动之间的不符之处
* 只读

### Memory

记录最终结果。

* 存放最终阶段的结果
* 记录重要决策
* 保留任务结果以供后续参考
* 没有 Tool 访问权限

### Orchestrator

协调整条流水线。

Orchestrator 控制阶段流转，但**不授予 Tool 权限**。

Tool 授权由 `ruder_ai/core/executor.py` 中的 `ToolExecutor` 依据
`ruder_ai/agents/permissions.py` 的确定性角色策略（`ROLE_TOOL_POLICIES`）强制执行。
Executor 在每次 Tool 调用时执行权威的运行时检查。

---

## Tool 权限强制

RUDER-AI 不把提示词中的指令当作授权边界。

每次 Tool 调用都会经过 `ToolExecutor`，它在执行前检查当前角色。

```text
Role Agent
    │
    ▼
Tool Call
    │
    ▼
ToolExecutor
    │
    ├── Allowed → Execute Tool
    └── Denied  → permission error
```

角色代理会把自身的明确角色传给 Executor。

如果某个角色试图使用未获准的 Tool，`ToolExecutor` 会返回确定性的 `permission` 错误。

### 双层保护

Coder 在系统提示词中只会收到自己获准的 Tool。

但提示词并不被视为最终的安全边界。

Executor 在运行时执行权威的权限检查。

```text
LLM Tool visibility
        +
Runtime Tool authorization
        =
Role-enforced Tool access
```

为保持向后兼容，使用 `role=None` 的旧版 `ToolExecutor` 直接调用者仍然不受限制。

---

## 验证模型

RUDER-AI 有意将实现与验证分离。

而不是：

```text
LLM → Modify → "Done"
```

预期的流程是：

```text
Plan
  ↓
Modify
  ↓
Verify
  ↓
Review
  ↓
Record
```

因此，系统力图以独立观察到的项目状态为依据判定完成，而不是仅仅依赖 Coder
声称任务已成功的说法。

---

## 语言模型

RUDER-AI 基于以下模型提供各语言的模型配置：

```text
qwen2.5-coder:7b-instruct
```

当前配置如下：

| 模型          | 语言      | 用途               |
| ------------- | --------- | ------------------ |
| `ruder-ai-ko` | 한국어      | Korean RUDER-AI   |
| `ruder-ai-cn` | 简体中文     | Chinese RUDER-AI  |
| `ruder-ai-en` | English  | English RUDER-AI  |
| `ruder-ai-jp` | 日本語      | Japanese RUDER-AI |

每个模型使用相同的 RUDER-AI Code Mode 架构，同时强制其配置的响应语言。

技术代码、标识符、类名、方法名以及源语言注释保持其原有的源语言。

自然语言说明、推理与指令则以配置的语言生成。

### 模型配置

`Modelfiles/` 下的 Ollama Modelfile 在模型层面设置采样参数：

```text
Temperature: 0.7
Context window: 16384
Base model: qwen2.5-coder:7b-instruct
```

Python 运行时（`RuderAISettings`）为代理执行使用自己的确定性默认值，并覆盖请求级
的采样选项：

```text
Temperature: 0.1
Context window: 16384
Max tokens: 3072
```

因此，运行时的实际采样遵循 `RuderAISettings`（可通过 `RUDER_AI_TEMPERATURE` 等环境
变量或 `ruder-ai config` 配置），而 Modelfile 中的数值只在没有显式请求选项时调用
模型才生效。

这些模型的用途：

* RUDER-AI CODE MODE
* `READ_ONLY_INSPECT`

日常对话单独处理，不经过这些 Code Mode 配置。

---

## 各语言的模型文件

模型文件结构示例：

```text
Modelfiles/
├── ruder-ai-ko
├── ruder-ai-cn
├── ruder-ai-en
└── ruder-ai-jp
```

用 Ollama 创建模型（`-f` 路径相对于本仓库根目录）：

```bash
ollama create ruder-ai-ko -f Modelfiles/ruder-ai-ko
ollama create ruder-ai-cn -f Modelfiles/ruder-ai-cn
ollama create ruder-ai-en -f Modelfiles/ruder-ai-en
ollama create ruder-ai-jp -f Modelfiles/ruder-ai-jp
```

运行特定语言的模型：

```bash
ollama run ruder-ai-ko
```

```bash
ollama run ruder-ai-en
```

```bash
ollama run ruder-ai-jp
```

```bash
ollama run ruder-ai-cn
```

---

## 感知项目的编码

RUDER-AI 不假定某个特定的编程生态。

在做出改动之前，它会先检查真实项目并确定：

* 编程语言
* 构建系统
* 框架
* 项目结构
* 既有约定
* 配置文件与清单文件的格式
* 相关 API 与依赖

代理应遵循项目中已有的约定，而不是另起一套无关的架构。

对于如下配置文件与清单文件：

```text
package.json
pom.xml
plugin.yml
Cargo.toml
```

RUDER-AI 遵循目标项目已经确立的约定。

---

## 索引性能

索引构建是每个任务之前都要发生的环节，因此也是必须做到最快的部分。在本仓库上
实测（192 个文件，1,300 个符号，Windows，CPython 3.13）：

| 操作 | 之前 | 之后 |
|---|---|---|
| 编辑单个文件（`refresh_file`） | 302 ms | 3.7 ms |
| 完整索引重建，warm 状态 | 294 ms | 39 ms |
| `ProjectScanner.scan` | 115 ms | 10 ms |
| `FileResolver.resolve_many` | 114 ms | 5 ms |
| 上下文组装 | 17 ms | 10 ms |
| 文件系统签名（每次请求） | 9.6 ms | 4 ms |

改动了什么，以及每项为何是安全的：

* **增量索引现在真正生效了。** `AIAgent.refresh_file` 调用了
  `SymbolIndexer.update_file`，而该方法并不存在。每一次「增量」刷新都会抛出
  `AttributeError`，调用方将其吞掉并把 `project_index = None` 设置进去 —— 于是编辑一个
  文件就悄悄触发了整个工作区的重新扫描与重新解析。`SymbolIndexer.update_file` 与
  `ReferenceIndex.update_file` 现已存在，并且与完整重建得到逐字节相同的状态
  （已针对编辑、删除、语法错误和 Java 文件验证）。
* **引用索引仅在符号名发生移动时才重建。** 它以全局符号名集合为依据，因此只修改
  函数体的编辑可以只作用于单个文件；新增或删除定义仍会重新推导整个索引。
* **被忽略的目录在遍历过程中就被剪掉了**，而不是打开后再过滤。`node_modules`、
  `.venv`、`build/` 等会被跳过，并且这些规则是针对工作区*内部*的路径来匹配的 ——
  位于名为 `out` 的目录下的检出不再产生空索引。
* **派生数据按文件缓存**，键为 `(path, size, mtime_ns)` —— 与工作区签名判断是否需要
  重建时所用的三元组完全一致，因此缓存不可能比索引更旧。完整构建对每个文件只读取
  一次而非三次，未变更的文件既不重新分词也不重新解析。
* **分词与评分避免了 Python 层的逐字符开销。** token 估算器中的韩文字符统计是一次
  C 层正则扫描，并带有 ASCII 快速路径；无法拆分的 token 会跳过 CamelCase 拆分；
  不区分大小写的类型查找使用预先计算的小写名称。

等价性证明位于 `tests/test_v755_performance.py`：AST 遍历会针对仓库中每个模块与
`ast.walk` 对照，tokenizer 在数千个随机输入上与前代实现对照，增量索引器则与完整
重建对照。

随时可以用与上表相同的方式重新测量：

```bash
ruder-ai bench --dir . --reps 3
```

每个阶段在计时前都会预热，并以 best-of-N 报告；每次编辑的刷新在一次性副本中执行，
因此绝不会改动你的工作树。

---

## 轮次意图

一句问候不是一个任务。`안녕` 曾一路传到 planner，它为 `hello_handler` 模块编造出
一个 `write_file` —— 仅仅因为有人打了招呼，代理就修改了一个仓库。planner 已经为
检索计算过 `intent`，但在任何改动之前没有环节去查看它，因此对于一个什么都没要求的
轮次，模型可以随意编排它想做的计划。

`ruder_ai/core/turn_intent.py` 把每个轮次归为三类之一：

| 类别 | 示例 | 可否改动文件 |
|---|---|---|
| `CONVERSATIONAL` | `안녕`、`hi`、`thanks!` | 否 |
| `QUESTION` | `이게 왜 이렇게 짜여 있어?`、`which module handles login?` | 否 |
| `ACTION` | `auth.py 고쳐줘`、`Add a test for login` | 是 |

该判定在 **executor** 中强制执行，而不在 planner 中，因为写计划的正是同一个模型：
一个能编造计划的模型，同样也能编造出支持它的理由。`ToolExecutor._check_turn_intent`
会在 `write_file`、`append_file`、`patch_file`、`delete_file`、`move_file`、
`apply_patch` 中任意一个执行前立即运行，并以 `MUTATION_REFUSED_MESSAGE` 拒绝，同时
指出什么样的措辞才能解锁这次改动。只读 Tool 依然可用 —— 关于项目的问题是打开文件的
正当理由，只是永远不是修改文件的理由。

分类器有意偏向拒绝。把一个轮次误判为对话，代价只是用更明确的措辞重试一次；而把一个
轮次误判为可执行，则会悄无声息地改掉你的代码。拉丁文问候按单词边界匹配，因此
`this`/`which`/`him` 不会被读成 `hi`。

`AIAgent.process_task` 会把 `CONVERSATIONAL` 短路为一次不带 Tool、不带索引的 LLM
调用，所以一句问候的代价是一轮往返，而不是会产出「没有文件被改动」的完整
Explorer→Planner→Coder→Tester→Reviewer 流程。

---

## 项目综述

「이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘」曾只回答了 **190 个文件中的
2 个**，而输出中没有任何迹象表明其余 188 个被跳过了。这里存在两个问题：没有一种方式
把 190 个文件放进一个提示词；以及覆盖率从未被报告，于是残缺的答案看起来像是完整的
答案。

综述直接基于项目索引构建，因此天生完整且不产生模型调用：

```bash
ruder-ai survey --dir .
```

```
- 파일: **199/199**개 (1480 KiB) (100%)

## CLI 엔트리포인트 (1개)

### `ruder_ai/main.py`
- 226줄 / 8,975 bytes / .py
- 기능: RUDER-AI CLI Main Entrypoint.
- 주요 심볼: `run_agent_loop`, `start`, `bench`, `where`, `survey`, `gui`, `config`
```

每个文件的说明都来自它自己的 docstring 和 AST（说明它*为何存在*，而不是 LLM 猜测它
包含什么），并按从目录、文件名和包结构推导出的角色分组。上例中的分组名称正是该 Tool
输出的字面标签（当前为韩文）。`covered/total` 这一行不是装饰：存在于索引中却无法读取
的文件会计入分母并在输出中点名，因此这个数字不会悄悄地表示「我们实际读到的那些」。

### 分两个层级，而非一个

要求*分析*一个项目和要求描述其中*每一个文件*是不同的请求，而用一段 48,000 字符的
倾倒来回答两者，既谈不上有帮助，也等同于两者都没回答。它们走各自的路径：

| 请求 | 回答 |
|---|---|
| 「모든 프로젝트를 분석해」、「파일마다 기능 일일히 말해줘」、「explain every file」 | 完整综述，每个文件一节 |
| 「이 프로젝트가 뭐 하는 곳이야?」、「analyze this codebase」 | 一屏概览：规模、顶层结构、角色直方图、入口点、最大模块 —— 并指出哪个请求能得到完整综述 |

两者都是确定性的，且不产生模型调用。无论哪种方式你都能拿到一份分析；但两者都不会回答
关于文件*改动*的问题，而那恰恰是只读请求过去会掉进去、结果却拿到一份别的东西的地方。

### 不必重读那堵墙就能继续追问

完整综述约 48,000 字符。在聊天面板里连续两次提供它不是答案，只是滚动，因此综述只
提供**一次**：重复请求会返回概览以及进一步收窄的方式，除非你明确要求再看完整版。而
点名了对象的请求只得到该对象：

| 你这样问 | 你会得到 |
|---|---|
| `ruder_ai/core/executor.py 는 어떤 일을 해?` | 该文件的完整说明 |
| `executor.py 자세히 설명해줘` | 同一个文件，按 basename 匹配 |

| `테스트 파일만 설명해줘` | 63 个测试文件的列表 |
| `core 디렉터리 자세히` | `core/` 下的 25 个文件 |
| `없는파일.py 설명해줘` | 概览，外加一句诚实的「未找到」 |

被点名的文件、角色或目录优先于概览，而编辑请求又优先于二者 —— `auth.py 고쳐줘`
是工作而非提问，而正是 executor 的轮次意图门控阻止它变成一次改动。

韩文在匹配前会先做助词归一化。自然语言中助词位于名词与动词之间 —— `프로젝트` + `를` +
` ` + `분석해` —— 因此用不含助词的标记去匹配会差一个字，导致「모든 프로젝트를 분석해」
无法路由。助词在匹配前被剥离，而不是把各种拼写变体逐一列举；该归一化器仅用于路由，
并且刻意不与问候分类器共用 —— 在后者那里，`하이` 确实必须保持为 `하이`。

---

## 出处与副本分歧

有一次完整的工作会话因此丢失。改动写进了一个 RUDER-AI 检出，而可编辑安装让代理
导入的是*另一份*副本，于是每次改动看起来都毫无作用 —— 却没有任何警告。代理运行得
很好，它只是运行了错误的代码。

```bash
ruder-ai where
```

```
RUDER-AI 실행 위치 : E:\etc\ruder-ai\Ruder-AI
import 경로       : E:\etc\ruder-ai\Ruder-AI\ruder_ai
현재 작업 디렉터리 : E:\etc\ruder-ai\workspace
버전              : 7.5.9-survey-followups
⚠️  경고: 실행 중인 코드와 작업 디렉터리가 서로 다른 복사본입니다.
```

`start` 和 `gui` 在启动时也会打印同样的头部信息。不止当前进程，
`~/.ruder_ai/provenance.json` 还会记住本机上出现过的每一个检出，以及六个身份文件的
哈希（`VERSION`、`main.py`、`executor.py`、`agent.py`、`scanner.py`、`app_gui.py`）。
从第二份检出运行时，它会报告哪些文件存在差异；已经消失的检出则被静默丢弃 ——
陈旧但未被改动的副本不会产生噪音。

所报告的版本是检出的 `VERSION` 文件内容，而不是打包版本（`0.1.0`）或
`AIAgent.__version__`：那是 README 和 Modelfile 所引用的字符串，也是用户会引用的
那个版本。

如果你为性能工作维护第二个检出，`sync_perf_clone.py` 会在从不删除文件的前提下把源文件
复制过去：

```bash
python sync_perf_clone.py . ..\workspace --dry   # 预览
python sync_perf_clone.py . ..\workspace
```

---

## Code Mode

RUDER-AI Code Mode 面向各种语言和框架的软件工程任务而设计。

典型任务包括：

* 理解现有代码库
* 找到相关文件
* 规划实现改动
* 编写新代码
* 修补现有代码
* 运行项目命令
* 诊断错误
* 验证构建与测试
* 审查改动
* 从失败的实现尝试中恢复

本系统并不局限于某一种生态。

---

## 只读检视

`READ_ONLY_INSPECT` 是一种非破坏性模式。

即使用户的请求包含编码动作，代理在此阶段也不得修改文件。

而应当：

1. 检视项目
2. 分析所请求的改动
3. 识别相关文件与代码
4. 返回其发现

文件改动保留给拥有相应 Tool 权限的阶段。

---

## 网页搜索依据

当使用 `web_search` 之类的信息检索 Tool 时，其结果成为回答的权威来源。

模型必须：

* 以检索结果为依据给出最终回答
* 不引入与之冲突的记忆内容
* 不臆造结果中不存在的事实
* 在检索结果未包含所问答案时明确说明

这旨在减少涉及当前文档、版本、API、价格、日期等易变信息时的无依据断言。

---

## 架构

在高层次上：

```text
                    ┌──────────────┐
                    │     User     │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Orchestrator │
                    └──────┬───────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
    Explorer            Planner             Coder
        │                  │                  │
        │                  │                  ▼
        │                  │             ToolExecutor
        │                  │                  │
        │                  │                  ▼
        │                  │              Project
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                        Tester
                           │
                           ▼
                       Reviewer
                           │
                           ▼
                        Memory
```

LLM 负责推理与决策，Tool 层负责具体的工作区操作。

---

## 设计原则

### 确定性执行

LLM 的输出不被视为某个操作已经发生的证据。

只有 Tool 操作真正成功执行之后，代理才可以声称该操作已完成。

### 职责分离

探查、规划、实现、测试、审查和记忆被划分为彼此独立的阶段。

### 独立验证

执行实现的组件，不能是判定实现是否成功的唯一组件。

### 显式授权

Tool 权限由 executor 强制执行，而非仅依赖自然语言指令。

### 感知项目

代理在假定目标项目的生态或约定之前，会先检视真实项目。

### 多语言支持

同一套 RUDER-AI 编码架构可以通过韩文、中文、英文和日文的专用模型配置对外提供。

---

## 状态

**当前流水线：** `v7.5.9`

**支持的模型配置：**

```text
ruder-ai-ko
ruder-ai-cn
ruder-ai-en
ruder-ai-jp
```

RUDER-AI 是一个持续演进中的项目，目标是把 LLM 推理与确定性 Tool 执行、显式权限、
独立验证以及多语言支持结合起来，构建实用的自主编码系统。
