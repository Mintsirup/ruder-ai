from pathlib import Path

from ruder_ai.core.executor import ToolExecutor
from ruder_ai.core.failure_policy import FailurePolicy
from ruder_ai.core.goal_planner import Plan, PlanTask


def _executor(tmp_path):
    return ToolExecutor(
        llm=None, skill_registry=None, workspace_path=tmp_path,
        goal_planner=None, agent=None,
    )


def test_extract_failure_type_classifies_git_without_bracket():
    failure = "git_diff failed: fatal: Not a git repository (or any of the parent directories)"
    assert ToolExecutor._extract_failure_type(failure) == ToolExecutor.ERROR_TYPE_VALIDATION


def test_post_process_plan_normalizes_git_tools_for_non_git_workspace(tmp_path):
    ex = _executor(tmp_path)
    plan = Plan(goal="inspect", tasks=[PlanTask(order=1, description="diff", tool="git_diff")])
    normalized = ex._post_process_plan(plan)
    assert normalized.tasks[0].tool == "list_directory"


def test_post_process_plan_keeps_git_tools_in_git_workspace(tmp_path):
    (tmp_path / ".git").mkdir()
    ex = _executor(tmp_path)
    plan = Plan(goal="inspect", tasks=[PlanTask(order=1, description="diff", tool="git_diff")])
    normalized = ex._post_process_plan(plan)
    assert normalized.tasks[0].tool == "git_diff"


def test_failure_policy_retry_is_reachable():
    decision = FailurePolicy.decide(
        error_type="unknown", attempt=0, max_retries=1,
        max_replans=2, replans=0,
    )
    assert decision.action == "retry"


def test_failure_policy_replans_after_retry_budget():
    decision = FailurePolicy.decide(
        error_type="unknown", attempt=1, max_retries=1,
        max_replans=2, replans=0,
    )
    assert decision.action == "replan"


def test_classify_subprocess_validation_errors():
    msg = "command failed: old_str is not found; diff is required"
    assert ToolExecutor._classify_error(None, msg) == ToolExecutor.ERROR_TYPE_VALIDATION


def test_fuzzy_patch_match_uses_unique_whitespace_insensitive_span(tmp_path):
    path = tmp_path / "Example.java"
    path.write_text("public class Example {\n    void play(File file) {\n        start(file);\n    }\n}\n", encoding="utf-8")
    ex = _executor(tmp_path)
    kwargs = {
        "file_path": "Example.java",
        "old_str": "void   play(File file) {\n  start(file);",
        "new_str": "void play(File file) { start(file); }",
    }
    # Invoke through the private preparation path used by _execute_tool.
    # The fuzzy match is visible by calling it through a minimal fake skill.
    import asyncio
    class Skill:
        async def execute(self, **kw):
            return {"status": "success"}
    class Registry:
        def get_skill(self, name): return Skill()
    ex.skill_registry = Registry()
    ex._current_task = "modify Example.java"
    result = asyncio.run(ex._execute_tool("patch_file", kwargs))
    assert result["status"] == "success"

