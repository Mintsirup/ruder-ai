# RUDER-AI

**Languages:** [English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh.md)

**RUDER-AI** is a role-separated autonomous coding agent designed to turn natural-language tasks into real, verifiable changes inside a software workspace.

RUDER-AI combines LLM reasoning with deterministic Tool execution, role-based permissions, independent verification, and multi-language model configurations.

## Requirements

* Python 3.10 or newer
* [Ollama](https://ollama.com) running locally (default `http://127.0.0.1:11434`)
* `tkinter` — included with CPython on Windows/macOS; on Linux install
  `python3-tk` if the GUI is needed. The terminal agent and the test suite do
  not use it.

## Installation

```bash
git clone https://github.com/Mintsirup/ruder-ai.git
cd ruder-ai
python -m pip install -e .
```

This installs the `ruder-ai` CLI. Start the interactive agent with:

```bash
ruder-ai start --dir /path/to/your/project --model ruder-ai-ko
```

### Commands

| Command | What it does |
|---|---|
| `ruder-ai start` | Interactive agent on the target project |
| `ruder-ai gui` | Desktop editor + AI console |
| `ruder-ai where` | Print the checkout that is actually running, and warn if another clone has different code |
| `ruder-ai survey` | Describe every file in a project, grouped by role (no LLM call) |
| `ruder-ai bench` | Measure the hot paths: scan, index, plan, context, per-edit refresh |
| `ruder-ai config` | Open the settings window |

## Desktop GUI

RuderAI Studio is a Code-OSS style desktop app — file explorer, multi-tab editor,
AI console and an integrated terminal — built entirely on the Python standard
library's `tkinter`. There is no binary GUI wheel to install, so nothing outside
`pip install -e .` is required to run it.

```bash
ruder-ai gui      # open RuderAI Studio
ruder-ai config   # settings window: workspace, Ollama URL, model, temperature
```

Notes:

* Every run — CLI, GUI chat and the JSONL execution log in
  `.ruder_ai_logs/` — is terminated with an explicit `End Of Token` marker,
  so a transcript can always tell "the agent finished" from "the stream was
  cut off mid-task". In the log it is an `end_of_token` event.
* Output captured from child processes (`execute_code`, `execute_shell`, `git`,
  the verification runners) is forced to UTF-8 in the child. On Windows a
  piped process encodes stdout with the ANSI code page, so without this the
  agent reads its own Korean output back as mojibake and concludes the code
  it just wrote is broken.
* `tkinter` ships with CPython on Windows and macOS. Some slim Linux images
  need `python3-tk`; headless environments (servers, Termux) have no display and
  cannot run the GUI — use `ruder-ai start` there.
* The integrated terminal attaches to a **real TTY**: a PTY on Linux/macOS and a
  Win32 **ConPTY** pseudoconsole on Windows, so `vim`, `htop` and language REPLs
  behave normally. If a host exposes the ConPTY API but refuses to allocate a
  console host (containers, non-interactive sessions, some CI runners), Studio
  degrades to a piped shell and says so in the terminal — ordinary commands
  still work, full-screen programs will not.
* Both `gui` and `config` read and write the same `.ruder_ai_config.json` as the
  terminal agent, so a model or host change applies everywhere.
* The editor does syntax highlighting for Python, the C-like family (JS/TS, C#,
  Java, C/C++, Go, Rust, Kotlin, Swift, PHP, Lua), JSON, YAML, shell, CSS,
  markdown, XML/HTML and Ruby. Highlighting is debounced, and buffers that are
  very large or very long-line simply render unhighlighted rather than stalling.
* Find/replace: `Ctrl+F` / `Ctrl+H`, `F3` and `Shift+F3` to repeat, `Esc` to
  close the panel or drop the selection. Options are case sensitivity, whole
  word, regular expressions, and an “오타 허용” (typo-tolerant) fallback that
  offers near matches when the exact search comes up empty. Invalid patterns
  are reported instead of raising, and the status line shows `Ln/Col`.
* Whole-word matching is Unicode-aware rather than regex `\b`, so it behaves
  correctly for Korean/CJK and for needles made of punctuation (`//` in a
  comment). Regular-expression search is multiline, so `^import` matches every
  line that starts with `import`.

## Development

Run the test suite from the repository root:

```bash
python -m pip install pytest pytest-asyncio
python -m pytest
```

`tests/conftest.py` turns on `RUDER_AI_BENCHMARK_FIXTURE_MODE` by default, which
selects the orchestrator's deterministic benchmark answers instead of calling a
live model. Set the variable yourself to override that (for example
`RUDER_AI_BENCHMARK_FIXTURE_MODE=0` to exercise the live-LLM code path).

## Translations

This README is also available in Korean, Japanese and Simplified Chinese. Each
translation mirrors this file section for section; when one is out of date, the
English file is the source of truth.

| Language | File |
|---|---|
| English | [README.md](README.md) |
| 한국어 | [README.ko.md](README.ko.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| 简体中文 | [README.zh.md](README.zh.md) |

## Features

* Autonomous coding workflow
* Role-separated agent pipeline
* Deterministic Tool permission enforcement
* Turn-intent gating — a greeting never reaches the filesystem
* Complete, coverage-reported project surveys and overviews
* Independent project verification
* Claim verification against actual files and diffs
* Project-aware code generation
* Replanning and recovery
* Web search and information retrieval
* Memory for final task outcomes
* Multi-language model configurations
* Korean, Chinese, English, and Japanese language support
* Built-in benchmark (`ruder-ai bench`) for the paths every task pays for

---

## v7.5.9 Agent Pipeline

```text
Explorer → Planner → Coder → Tester → Reviewer → Memory
                 \_______________________________/
                           Orchestrator
```

RUDER-AI separates the coding workflow into specialized stages.

### Explorer

Read-only project exploration.

* Inspects project structure
* Searches files
* Inspects symbols and references
* Identifies the project's language and framework
* Does not mutate files

### Planner

Builds the implementation plan.

* Analyzes the requested task
* Determines the required changes
* Identifies relevant files and components
* Produces a structured plan before implementation

### Coder

Applies the planned changes.

* Creates and modifies files
* Applies patches
* Executes permitted development Tools
* Implements the planned solution

Verification is deliberately deferred to the Tester stage.

### Tester

Independently verifies the implementation.

* Runs `verify_project`
* Inspects project state
* Checks whether the implementation actually works
* Does not mutate files

The Coder is therefore not the final authority on whether its own work succeeded.

### Reviewer

Reviews the result against reality.

* Inspects changed files
* Inspects diffs
* Checks implementation claims
* Identifies discrepancies between reported and actual changes
* Read-only

### Memory

Records the final result.

* Stores final-stage outcomes
* Records important decisions
* Preserves task results for future context
* Has no Tool access

### Orchestrator

Coordinates the complete pipeline.

The Orchestrator controls stage flow but does **not** grant Tool permissions.

Tool authorization is enforced by `ToolExecutor` using the deterministic role
policies defined in `ruder_ai/agents/permissions.py` (`ROLE_TOOL_POLICIES`).
The executor performs the authoritative runtime check on every Tool call.

---

## Tool Permission Enforcement

RUDER-AI does not treat prompt instructions as an authorization boundary.

Every Tool call passes through `ToolExecutor`, which checks the active role before execution.

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

Role agents provide their explicit role to the executor.

If a role attempts to use a Tool that is not permitted, `ToolExecutor` returns a deterministic `permission` error.

### Two-layer protection

The Coder receives only its permitted Tools through the LLM system prompt.

However, the prompt is not considered the final security boundary.

The Executor performs the authoritative permission check at runtime.

```text
LLM Tool visibility
        +
Runtime Tool authorization
        =
Role-enforced Tool access
```

For backward compatibility, direct legacy `ToolExecutor` callers using `role=None` remain unrestricted.

---

## Verification Model

RUDER-AI intentionally separates implementation from verification.

Instead of:

```text
LLM → Modify → "Done"
```

the intended workflow is:

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

The system therefore attempts to base completion on independently observed project state rather than relying solely on the Coder's claim that the task succeeded.

---

## Language Models

RUDER-AI provides language-specific model configurations based on:

```text
qwen2.5-coder:7b-instruct
```

The current configurations are:

| Model         | Language | Purpose           |
| ------------- | -------- | ----------------- |
| `ruder-ai-ko` | 한국어      | Korean RUDER-AI   |
| `ruder-ai-cn` | 简体中文     | Chinese RUDER-AI  |
| `ruder-ai-en` | English  | English RUDER-AI  |
| `ruder-ai-jp` | 日本語      | Japanese RUDER-AI |

Each model uses the same RUDER-AI Code Mode architecture while enforcing its configured response language.

Technical code, identifiers, class names, method names, and source-language comments remain in their appropriate source language.

Natural-language explanations, reasoning, and instructions are produced in the configured language.

### Model configuration

The Ollama Modelfiles under `Modelfiles/` set sampling at the model level:

```text
Temperature: 0.7
Context window: 16384
Base model: qwen2.5-coder:7b-instruct
```

The Python runtime (`RuderAISettings`) uses its own deterministic defaults for
agent execution and overrides the request-level sampling options:

```text
Temperature: 0.1
Context window: 16384
Max tokens: 3072
```

As a result, effective runtime sampling follows `RuderAISettings`
(configurable via `RUDER_AI_TEMPERATURE` and related environment variables or
`ruder-ai config`), while the Modelfile value applies only where the model is
invoked without explicit request options.

The models are intended for:

* RUDER-AI CODE MODE
* `READ_ONLY_INSPECT`

Everyday conversation is handled separately and is not routed through these Code Mode configurations.

---

## Language-Specific Model Files

Example model structure:

```text
Modelfiles/
├── ruder-ai-ko
├── ruder-ai-cn
├── ruder-ai-en
└── ruder-ai-jp
```

Create the models with Ollama (the `-f` path is relative to this repository root):

```bash
ollama create ruder-ai-ko -f Modelfiles/ruder-ai-ko
ollama create ruder-ai-cn -f Modelfiles/ruder-ai-cn
ollama create ruder-ai-en -f Modelfiles/ruder-ai-en
ollama create ruder-ai-jp -f Modelfiles/ruder-ai-jp
```

Run a language-specific model:

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

## Project-Aware Coding

RUDER-AI does not assume a specific programming ecosystem.

Before making changes, it is expected to inspect the actual project and determine:

* Programming language
* Build system
* Framework
* Project structure
* Existing conventions
* Configuration and manifest formats
* Relevant APIs and dependencies

The agent should follow conventions already present in the project rather than inventing an unrelated architecture.

For configuration and manifest files such as:

```text
package.json
pom.xml
plugin.yml
Cargo.toml
```

RUDER-AI follows the conventions already established by the target project.

---

## Index Performance

Indexing a project is the one thing that happens before every task, so it is
the part that had to get fast. Measured on this repository (192 files,
1,300 symbols, Windows, CPython 3.13):

| Operation | Before | After |
|---|---|---|
| One file edit (`refresh_file`) | 302 ms | 3.7 ms |
| Full index rebuild, warm | 294 ms | 39 ms |
| `ProjectScanner.scan` | 115 ms | 10 ms |
| `FileResolver.resolve_many` | 114 ms | 5 ms |
| Context assembly | 17 ms | 10 ms |
| Filesystem signature (per request) | 9.6 ms | 4 ms |

What changed, and why each was safe:

* **Incremental indexing actually works now.** `AIAgent.refresh_file` called
  `SymbolIndexer.update_file`, which did not exist. Every "incremental"
  refresh raised `AttributeError`, the caller swallowed it and set
  `project_index = None` — so a one-file edit silently forced a full workspace
  rescan and re-parse. `SymbolIndexer.update_file` and
  `ReferenceIndex.update_file` now exist and land on byte-identical state to a
  full rebuild (verified against one, including edits, deletions, syntax
  errors and Java files).
* **The reference index is only rebuilt when a symbol name moves.** It keys
  off the global name set, so an edit that only changes a function body can
  be applied to the single file; adding or removing a definition still
  re-derives the whole thing.
* **Ignored directories are pruned during the walk** instead of being opened
  and filtered afterwards. `node_modules`, `.venv`, `build/` and friends are
  skipped, and the rules are matched against paths *inside* the workspace —
  a checkout that happens to live under a directory named `out` no longer
  produces an empty index.
* **Derived data is cached per file**, keyed on `(path, size, mtime_ns)` —
  the same triple the workspace signature already used to decide whether a
  rebuild is needed, so the cache can never be staler than the index. One
  full build reads each file once instead of three times, and unchanged files
  are neither re-tokenized nor re-parsed.
* **Tokenizing and scoring avoid Python-level per-character work.** Hangul
  counting in the token estimator is a single C-level regex pass with an
  ASCII fast path; CamelCase splitting is skipped for tokens that cannot
  split; the case-insensitive type lookup uses precomputed lowercased names.

The equivalence proofs live in `tests/test_v755_performance.py` — the AST
walk is checked against `ast.walk` over every module in the repo, the
tokenizer against its predecessor on thousands of random inputs, and the
incremental indexers against a full rebuild.

Re-measure any time with the same numbers the table was built from:

```bash
ruder-ai bench --dir . --reps 3
```

Each stage is warmed before it is timed and reported as best-of-N, and the
per-edit refresh runs in a throwaway copy so your tree is never touched.

---

## Turn Intent

A greeting is not a task. `안녕` once reached the planner, which invented a
`write_file` for a `hello_handler` module — the agent modified a repository
because someone said hello. The planner already computed an `intent` for
retrieval, but nothing consulted it before a mutation, so the model was free
to plan whatever it liked for a turn that asked for nothing.

`ruder_ai/core/turn_intent.py` classifies every turn into one of three kinds:

| Kind | Example | May touch a file |
|---|---|---|
| `CONVERSATIONAL` | `안녕`, `hi`, `thanks!` | no |
| `QUESTION` | `이게 왜 이렇게 짜여 있어?`, `which module handles login?` | no |
| `ACTION` | `auth.py 고쳐줘`, `Add a test for login` | yes |

The decision is enforced in the **executor**, not the planner, because the same
model writes the plan: a model that invents a plan would also invent its own
justification. `ToolExecutor._check_turn_intent` runs immediately before any of
`write_file`, `append_file`, `patch_file`, `delete_file`, `move_file` or
`apply_patch` and refuses with `MUTATION_REFUSED_MESSAGE`, which names the
wording that would unlock the change. Read-only tools stay available — a
question about the project is a legitimate reason to open files, just never to
change them.

The classifier is deliberately biased toward refusing. A turn misread as
conversational costs one retry with clearer wording; a turn misread as
actionable silently edits your code. Latin greetings are matched on word
boundaries, so `this`/`which`/`him` never read as `hi`.

`AIAgent.process_task` short-circuits `CONVERSATIONAL` to a single LLM call
with no tools and no index, so a greeting costs one round trip instead of a
full Explorer→Planner→Coder→Tester→Reviewer pass that would have produced "no
files changed".

---

## Project Survey

"이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘" once answered about
**2 files out of 190**, with nothing in the output saying the other 188 had
been skipped. Two things were wrong: no way to hold 190 files in a prompt, and
coverage that was never reported — so a partial answer looked like a complete
one.

A survey is built from the project index instead, which makes it complete by
construction and free of model calls:

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

Each file is described from its own docstring and AST (what it is *for*, not
what an LLM guesses it contains), grouped by role derived from the directory,
the filename and the package layout. The `covered/total` line is not
decoration: a file that exists in the index but cannot be read is counted in
the denominator and named in the output, so the number cannot quietly mean
"the ones we got to".

### Two tiers, not one

Asking to *analyse* a project and asking to describe *every file* in it are
different requests, and answering both with a 48 000-character dump is as
unhelpful as answering neither. They route separately:

| Request | Answer |
|---|---|
| "모든 프로젝트를 분석해", "파일마다 기능 일일히 말해줘", "explain every file" | full survey, one section per file |
| "이 프로젝트가 뭐 하는 곳이야?", "analyze this codebase" | one-screen overview: size, top-level layout, role histogram, entrypoints, largest modules — and it names the request that gets you the full survey |

Both are deterministic and cost no LLM call. Either way you get an analysis;
neither answers a question about file *changes*, which is what a read-only
request used to fall through to and receive instead.

### Following up without re-reading the wall

A full survey is ~48 000 characters. Serving it twice in a row in a chat pane
is not an answer, it is a scroll, so the survey is delivered **once**: a
repeat request returns the overview and the ways to go narrower, unless you
explicitly ask for the full thing back. And a request that names something
gets just that:

| You ask | You get |
|---|---|
| `ruder_ai/core/executor.py 는 어떤 일을 해?` | that file, in full detail |
| `executor.py 자세히 설명해줘` | the same, by basename |
| `테스트 파일만 설명해줘` | the 63 test files, listed |
| `core 디렉터리 자세히` | the 25 files under `core/` |
| `없는파일.py 설명해줘` | the overview, with an honest "not found" |

A named file, role or directory outranks the overview, and an edit request
outranks both — `auth.py 고쳐줘` is work, not a question, and the executor's
turn-intent gate is what keeps it from becoming a mutation.

Korean is matched after particle normalisation. Natural text puts josa
between the noun and the verb — `프로젝트` + `를` + ` ` + `분석해` — so
matching a particle-free marker against it missed by one character, and
"모든 프로젝트를 분석해" was unroutable. The particles are stripped before
matching rather than enumerated as spelling variants; the normaliser is
confined to routing and is deliberately not shared with the greeting
classifier, where `하이` really does have to stay `하이`.

---

## Provenance and Clone Divergence

A whole work session was lost to this. Edits went into one RUDER-AI checkout
while an editable install made the agent import a *different* copy, so every
change appeared to do nothing — and nothing warned about it. The agent ran
fine; it just ran the wrong code.

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

`start` and `gui` print the same header at startup. Beyond the current
process, `~/.ruder_ai/provenance.json` remembers every checkout seen on the
machine together with a hash of six identity files (`VERSION`, `main.py`,
`executor.py`, `agent.py`, `scanner.py`, `app_gui.py`). Running from a second
clone reports which files differ, and a vanished clone is dropped silently — a
stale-but-untouched copy produces no noise.

The version reported is the checkout's `VERSION` file, not the packaging
version (`0.1.0`) or `AIAgent.__version__`: it is the string the README and the
Modelfiles refer to, so it is the one a user would quote.

If you maintain a second checkout for performance work, `sync_perf_clone.py`
copies source files into it without ever deleting:

```bash
python sync_perf_clone.py . ..\workspace --dry   # preview
python sync_perf_clone.py . ..\workspace
```

---

## Code Mode

RUDER-AI Code Mode is designed for software engineering tasks across different languages and frameworks.

Typical tasks include:

* Understanding an existing codebase
* Finding relevant files
* Planning implementation changes
* Writing new code
* Patching existing code
* Running project commands
* Diagnosing errors
* Verifying builds and tests
* Reviewing changes
* Recovering from failed implementation attempts

The system is not restricted to a particular ecosystem.

---

## Read-Only Inspection

`READ_ONLY_INSPECT` is a non-mutating mode.

Even when the user's request contains a coding action, the agent must not modify files during this stage.

Instead, it should:

1. Inspect the project
2. Analyze the requested change
3. Identify relevant files and code
4. Return its findings

File mutation is reserved for stages that have the appropriate Tool permissions.

---

## Web Search Grounding

When an information-retrieval Tool such as `web_search` is used, the resulting information becomes the authoritative source for the response.

The model must:

* Ground the final response in the retrieved result
* Avoid introducing conflicting remembered information
* Avoid inventing facts not present in the result
* Explicitly state when the retrieved information does not contain the requested answer

This is intended to reduce unsupported claims when working with current documentation, versions, APIs, prices, dates, or other changing information.

---

## Architecture

At a high level:

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

The LLM provides reasoning and decision-making, while the Tool layer performs concrete workspace operations.

---

## Design Principles

### Deterministic execution

LLM output is not treated as proof that an operation happened.

A Tool operation must actually execute successfully before the agent can claim that the operation was completed.

### Separation of responsibilities

Exploration, planning, implementation, testing, review, and memory are separated into distinct stages.

### Independent verification

The component that performs an implementation is not the only component responsible for deciding whether the implementation succeeded.

### Explicit authorization

Tool permissions are enforced by the executor rather than relying exclusively on natural-language instructions.

### Project awareness

The agent inspects the actual project before making assumptions about its ecosystem or conventions.

### Multi-language support

The same RUDER-AI coding architecture can be exposed through dedicated Korean, Chinese, English, and Japanese model configurations.

---

## Status

**Current pipeline:** `v7.5.9`

**Supported model configurations:**

```text
ruder-ai-ko
ruder-ai-cn
ruder-ai-en
ruder-ai-jp
```

RUDER-AI is an ongoing project focused on building a practical autonomous coding system where LLM reasoning is combined with deterministic Tool execution, explicit permissions, independent verification, and multi-language support.
