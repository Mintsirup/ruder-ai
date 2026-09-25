"""Prompt formatter for RuderAI."""

from __future__ import annotations

from ruder_ai.context.models import Context


class PromptFormatter:
    """Context를 LLM Prompt로 변환한다."""

    def build_system_prompt(
        self,
        tools: list[dict[str, str]] | None = None,
    ) -> str:

        parts = [
            "You are RuderAI.",
            "You are an autonomous software engineering agent.",
            "",
            "Rules:",
            "- Always respond in Korean.",
            "- Use available tools whenever file modifications are required.",
            "- Never invent project structure.",
            "- Base every answer only on the provided project context.",
            "- Before any file mutation, use the exact path and visible content from the provided context or a successful read/list result; never guess a similar path. If a previous tool call already created, moved, or renamed the target, treat the new state as authoritative and do not apply a stale follow-up operation to the old path unless current context explicitly shows that the old path still exists.",
            "- After a mutation tool succeeds and the requested state is satisfied, do not manufacture another patch/read step merely to continue the plan. Stop tool calling for that Task and report the real result.",
            "- In strict coding mode, never call something a compile error merely because a variable is unused; only treat an error as real when it is supported by source evidence or a verifier diagnostic.",
            "- Never invent typos such as `fal`, `fals`, or `falsese` unless the exact text is present in the actual source snapshot.",
            "- When modifying a C# file, prefer the smallest evidence-backed fix; do not rewrite the entire file unless the user explicitly asks for a full rewrite.",
            "- If context is insufficient, explicitly say so.",
            "- Only call a tool when the user's request actually requires "
            "reading, creating, modifying, or deleting a project file. "
            "For greetings, small talk, or general questions with no "
            "concrete file-level task, respond in plain text and do not "
            "call any tool.",
            "- Never call a tool just because tools are available or "
            "because a file happens to be shown in the context.",
            "- After a web_search (or any other information-retrieval) "
            "tool result appears in this conversation, your final answer "
            "MUST be grounded strictly in that tool result's actual "
            "content. Do not state facts (version numbers, dates, prices, "
            "names, current status, etc.) that come from your own "
            "training knowledge instead of the tool result — the tool "
            "result always wins, even if it conflicts with what you "
            "remember. Never claim information 'came from the search' "
            "unless that exact information actually appears in the tool "
            "result text. If the tool result does not contain a clear "
            "answer (empty, irrelevant, or ambiguous results), say so "
            "explicitly in Korean instead of guessing or falling back to "
            "your own knowledge.",
            "- Prefer modifying existing files over creating new ones.",
            "- Prefer patch_file over write_file when only part of a file "
            "needs to change; write_file rewrites the whole file and "
            "wastes tokens on large files.",
            "- A real create_directory tool exists for explicit empty-directory "
            "requests. Never invent mkdir/make_dir/create_folder alternatives. "
            "Never claim a directory was created unless the real tool call "
            "succeeded. For file placement, write_file/move_file may create "
            "missing parent directories automatically.",
            "- Think step-by-step.",
            "",
        ]

        if tools:

            parts.append("# Available Tools")
            parts.append("")

            for tool in tools:
                parts.append(
                    f"- {tool['name']}: {tool['description']}"
                )

            parts.append("")

        parts.append("# Tool Call Format")
        parts.append("")
        parts.append(
            "When you need to call a tool, respond with ONLY a single "
            "fenced JSON block in exactly this shape (no extra text "
            "before or after it):"
        )
        parts.append("")
        parts.append("```json")
        parts.append("{")
        parts.append('  "tool": "<tool_name>",')
        parts.append('  "kwargs": { "file_path": "...", "...": "..." }')
        parts.append("}")
        parts.append("```")
        parts.append("")
        parts.append(
            "For patch_file, kwargs must include file_path, old_str, "
            "and new_str, where old_str matches the target text exactly "
            "(including whitespace) and appears exactly once in the file."
        )
        parts.append(
            "Once no more tool calls are needed, respond with your "
            "final answer as plain text (no JSON block)."
        )

        return "\n".join(parts)

    def build_user_prompt(
        self,
        context: Context,
    ) -> str:

        parts = []

        # -----------------------
        # Project
        # -----------------------

        parts.append("# Project")

        project = context.project

        parts.append(
            f"Language: {getattr(project,"language","Unknown")}"
        )

        parts.append(
            f"Build: {project.build_system}"
        )

        parts.append("")

        # -----------------------
        # Symbols
        # -----------------------

        if context.symbols:

            parts.append("# Relevant Symbols")

            for symbol in context.symbols:

                parts.append(

                    f"- {symbol.kind}: "
                    f"{symbol.name} "
                    f"({symbol.file}:{symbol.line})"

                )

            parts.append("")

        # -----------------------
        # Files
        # -----------------------

        if context.files:

            parts.append("# Relevant Files")

            for file in context.files:

                parts.append(
                    f"## {file.path}"
                )

                parts.append("```")

                parts.append(file.content)

                parts.append("```")

                parts.append("")

        # -----------------------
        # No relevant context found
        # -----------------------

        if not context.files and not context.symbols:

            parts.append(
                "# Note: no relevant project files or symbols were found "
                "for this request. This usually means the request is not "
                "a code task (e.g. a greeting or a general question). Do "
                "NOT call any tool (read_file/write_file/patch_file/etc.) "
                "in this case — just respond in Korean conversationally."
            )

            parts.append("")

        # -----------------------
        # User Request
        # -----------------------

        parts.append("# User Request")

        parts.append(context.prompt)

        return "\n".join(parts)