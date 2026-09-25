# RUDER-AI

**RUDER-AI** is a role-separated autonomous coding agent designed to turn natural-language tasks into real, verifiable changes inside a software workspace.

RUDER-AI combines LLM reasoning with deterministic Tool execution, role-based permissions, independent verification, and multi-language model configurations.

## Features

* Autonomous coding workflow
* Role-separated agent pipeline
* Deterministic Tool permission enforcement
* Independent project verification
* Claim verification against actual files and diffs
* Project-aware code generation
* Replanning and recovery
* Web search and information retrieval
* Memory for final task outcomes
* Multi-language model configurations
* Korean, Chinese, English, and Japanese language support

---

## v7.2.2 Agent Pipeline

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

Tool authorization remains the responsibility of `ToolExecutor`.

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
    │
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
| `ruder-ai-zh` | 简体中文     | Chinese RUDER-AI  |
| `ruder-ai-en` | English  | English RUDER-AI  |
| `ruder-ai-ja` | 日本語      | Japanese RUDER-AI |

Each model uses the same RUDER-AI Code Mode architecture while enforcing its configured response language.

Technical code, identifiers, class names, method names, and source-language comments remain in their appropriate source language.

Natural-language explanations, reasoning, and instructions are produced in the configured language.

### Model configuration

The current model configurations use:

```text
Temperature: 0.7
Context window: 16384
Base model: qwen2.5-coder:7b-instruct
```

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

Create the models with Ollama:

```bash
ollama create ruder-ai-ko -f ruder-ai-ko
ollama create ruder-ai-cn -f ruder-ai-cn
ollama create ruder-ai-en -f ruder-ai-en
ollama create ruder-ai-jp -f ruder-ai-jp
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

**Current pipeline:** `v7.2.2`

**Supported model configurations:**

```text
ruder-ai-ko
ruder-ai-cn
ruder-ai-en
ruder-ai-jp
```

RUDER-AI is an ongoing project focused on building a practical autonomous coding system where LLM reasoning is combined with deterministic Tool execution, explicit permissions, independent verification, and multi-language support.
