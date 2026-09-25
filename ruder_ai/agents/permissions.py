"""Role-based Tool permissions for RuderAI.

The same LLM runtime may be shared by multiple role agents, but Tool access is
still enforced deterministically here.  Prompt instructions are only hints;
the executor is the authority.
"""
from __future__ import annotations

from dataclasses import dataclass


ROLE_TOOL_POLICIES: dict[str, frozenset[str]] = {
    "Explorer": frozenset({
        "read_file",
        "preview_patch",
        "list_directory",
        "search_symbol",
        "search_reference",
        "semantic_search",
        "git_status",
        "git_diff",
        "web_search",
        "web_fetch",
    }),
    "Coder": frozenset({
        # File mutation
        "write_file",
        "append_file",
        "patch_file",
        "delete_file",
        "move_file",
        "create_directory",
        "apply_patch",
        "backup_file",
        "restore_backup",
        "execute_shell",
        # Read/search
        "read_file",
        "preview_patch",
        "list_directory",
        "search_symbol",
        "search_reference",
        "semantic_search",
        # Coder does not run verification or arbitrary code execution.
        # Those responsibilities belong to TesterAgent.
        # Git
        "git",
        "git_status",
        "git_diff",
        "git_checkout",
        # Web
        "web_search",
        "web_fetch",
        # Patch helpers
        "generate_diff",
        "rollback_patch",
    }),
    "Tester": frozenset({
        "read_file",
        "preview_patch",
        "list_directory",
        "search_symbol",
        "search_reference",
        "semantic_search",
        "git_status",
        "git_diff",
        "verify_project",
    }),
    "Reviewer": frozenset({
        "read_file",
        "preview_patch",
        "list_directory",
        "search_symbol",
        "search_reference",
        "semantic_search",
        "git_status",
        "git_diff",
    }),
    "Memory": frozenset(),
}


@dataclass(frozen=True, slots=True)
class ToolPermissionResult:
    allowed: bool
    role: str
    tool: str
    message: str = ""


def is_tool_allowed(role: str | None, tool: str) -> bool:
    """Return whether *tool* is allowed for *role*.

    ``role=None`` is intentionally treated as unrestricted for backwards
    compatibility with callers that use ToolExecutor directly.
    """
    if role is None:
        return True
    return tool in ROLE_TOOL_POLICIES.get(role, frozenset())


def check_tool_permission(role: str | None, tool: str) -> ToolPermissionResult:
    if role is None:
        return ToolPermissionResult(True, "unrestricted", tool)

    allowed = is_tool_allowed(role, tool)
    if allowed:
        return ToolPermissionResult(True, role, tool)

    return ToolPermissionResult(
        False,
        role,
        tool,
        f"Role '{role}'에는 Tool '{tool}' 사용 권한이 없습니다.",
    )
