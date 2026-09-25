import asyncio
from pathlib import Path

from ruder_ai.agents.permissions import is_tool_allowed
from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.core.goal_planner import Goal, Plan, PlanTask
import tempfile
from ruder_ai.core.executor import ToolExecutor
from ruder_ai.indexer.detector import ProjectDetector
from ruder_ai.indexer.scanner import ProjectScanner
from ruder_ai.skills import SkillRegistry
from ruder_ai.skills.code_exec import ExecuteCodeSkill, ExecuteShellSkill
from ruder_ai.verify.verifier import AutoVerifier, _nearest_project_root


def test_execute_code_rejects_shell_commands(tmp_path):
    result = asyncio.run(ExecuteCodeSkill().execute("pip install pygame", workspace_path=str(tmp_path)))
    assert result["status"] == "error"
    assert result["guard"] == "shell_command_in_python_tool"


def test_execute_shell_is_registered_and_allowed_for_coder():
    registry = SkillRegistry()
    assert "execute_shell" in registry.list_skills()
    assert is_tool_allowed("Coder", "execute_shell")
    assert not is_tool_allowed("Tester", "execute_shell")


def test_execute_shell_runs_in_workspace(tmp_path):
    result = asyncio.run(ExecuteShellSkill().execute(
        "python -c \"print('forge-shell-ok')\"", workspace_path=str(tmp_path)
    ))
    assert result["status"] == "success"
    assert "forge-shell-ok" in result["stdout"]


def test_nested_project_root_is_selected_for_verification(tmp_path):
    outer = tmp_path
    (outer / "package.json").write_text('{"scripts":{"test":"node missing-test.js"}}', encoding="utf-8")
    project = outer / "project" / "music_player"
    source = project / "src" / "main" / "java" / "com" / "example" / "MusicPlayer.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package com.example; public class MusicPlayer { public static void main(String[] a) {} }\n",
        encoding="utf-8",
    )
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    assert _nearest_project_root(outer, ["project/music_player/src/main/java/com/example/MusicPlayer.java"]) == project.resolve()


def test_standalone_java_verification_does_not_run_outer_npm(tmp_path, monkeypatch):
    project = tmp_path / "project" / "music_player"
    source = project / "src" / "main" / "java" / "com" / "example" / "MusicPlayer.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package com.example; public class MusicPlayer { public static void main(String[] a) {} }\n",
        encoding="utf-8",
    )
    # Outer workspace intentionally contains a failing Node test setup.
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node definitely-missing.js"}}', encoding="utf-8")

    scanner = ProjectScanner(tmp_path)
    files = scanner.scan()
    outer_project = ProjectDetector().detect(tmp_path, files)
    report = asyncio.run(AutoVerifier().verify(
        outer_project,
        tmp_path,
        target_files=["project/music_player/src/main/java/com/example/MusicPlayer.java"],
    ))
    assert "npm_test" not in report.ran_tools
    assert "javac" in report.ran_tools


def test_requirements_only_python_gets_syntax_verification(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    (project / "requirements.txt").write_text("pygame\n", encoding="utf-8")
    scanner = ProjectScanner(project)
    files = scanner.scan()
    info = ProjectDetector().detect(project, files)
    assert info.language == "Python"
    assert info.build_system == "Python"
    report = asyncio.run(AutoVerifier().verify(info, project, target_files=["main.py"]))
    assert "python_compile" in report.ran_tools


def test_active_project_scope_blocks_mutation_outside_project(tmp_path):
    executor = ToolExecutor.__new__(ToolExecutor)
    executor.workspace_path = str(tmp_path)
    executor._active_project_root = "project/music_player"
    result = executor._active_scope_guard("write_file", {"file_path": "ruder_ai/core/agent.java"})
    assert result is not None
    assert result["guard"] == "active_project_scope"


def test_active_project_root_inferred_from_requirements_project(tmp_path):
    executor = ToolExecutor.__new__(ToolExecutor)
    executor.workspace_path = str(tmp_path)
    project = tmp_path / "project" / "music_player"
    project.mkdir(parents=True)
    (project / "requirements.txt").write_text("pygame\n", encoding="utf-8")
    assert executor._infer_project_root("project/music_player/src/main/python/main.py") == "project/music_player"


def test_changed_file_tracking_remains_safe_for_uninitialized_executor():
    changed = []
    executor = ToolExecutor.__new__(ToolExecutor)
    executor._track_changed_file("write_file", {"file_path": "a.py"}, {"status": "success"}, changed)
    assert changed == ["a.py"]


def test_realistic_music_player_fixture_has_correct_target_scope(tmp_path):
    project = tmp_path / "project" / "music_player"
    source = project / "src" / "main" / "java" / "com" / "example" / "MusicPlayer.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package com.example; public class MusicPlayer { private int position; }\n",
        encoding="utf-8",
    )
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    assert _nearest_project_root(tmp_path, [str(source.relative_to(tmp_path))]) == project.resolve()


def test_non_git_plan_replaces_git_status_with_list_directory():
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    from ruder_ai.core.goal_planner import Goal, Plan, PlanTask
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plan = Plan(goal=Goal(text="inspect"), tasks=[
            PlanTask(order=1, description="상태 확인", tool="git_status")
        ])
        normalized = AgentOrchestrator._normalize_plan_for_workspace(plan, tmp)
        assert normalized.tasks[0].tool == "list_directory"
        assert normalized.tasks[0].kwargs == {}


def test_shell_request_is_forced_to_execute_shell():
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    from ruder_ai.core.goal_planner import Goal, Plan
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plan = Plan(goal=Goal(text="env"), tasks=[])
        normalized = AgentOrchestrator._normalize_shell_request_plan(
            plan,
            "python --version과 pip --version을 셸 명령으로 확인해줘",
        )
        assert normalized.tasks[0].tool == "execute_shell"
        assert normalized.tasks[0].kwargs["command"] == "python --version && pip --version"


def test_stale_preview_after_patch_is_successful_noop():
    import asyncio
    from ruder_ai.core.executor import ToolExecutor

    class DummySkill:
        async def execute(self, **kwargs):
            raise AssertionError("should not execute stale preview")

    class DummyRegistry:
        def get_skill(self, name):
            return DummySkill() if name == "preview_patch" else None

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.txt"
        path.write_text("hello NEW world", encoding="utf-8")
        ex = ToolExecutor(
            llm=None,
            skill_registry=DummyRegistry(),
            workspace_path=tmp,
            max_steps=1,
        )
        async def run():
            ex._active_role = None
            ex._current_task = "x.txt 수정"
            return await ex._execute_tool(
                "preview_patch",
                {"file_path": "x.txt", "old_str": "hello OLD", "new_str": "hello NEW"},
            )
        result = asyncio.run(run())
        assert result["status"] == "success"
        assert result["stale_preview"] is True


def test_python_execution_auto_creates_venv(tmp_path):
    code = "import sys; print(sys.prefix != sys.base_prefix)"
    result = asyncio.run(ExecuteCodeSkill().execute(code, workspace_path=str(tmp_path), timeout=30))
    assert result["status"] == "success"
    assert result["stdout"].strip() == "True"
    assert (tmp_path / ".venv").exists()


def test_shell_python_command_uses_project_venv_and_deactivates(tmp_path):
    result = asyncio.run(ExecuteShellSkill().execute("python -c \"import sys; print(sys.prefix != sys.base_prefix)\"", workspace_path=str(tmp_path), timeout=30))
    assert result["status"] == "success"
    assert result["venv"] is True
    assert result["stdout"].strip() == "True"
    # Activation/deactivation happens inside the child shell; the parent process stays unchanged.
    assert "activate" not in result["executed_command"].lower()
    assert "deactivate" not in result["executed_command"].lower()


def test_shell_pip_resolves_inside_project_venv(tmp_path):
    result = asyncio.run(ExecuteShellSkill().execute("python -m pip --version", workspace_path=str(tmp_path), timeout=30))
    assert result["status"] == "success"
    assert str(tmp_path / ".venv") in result["stdout"] or "/.venv/" in result["stdout"] or "\\.venv\\" in result["stdout"]


def test_non_git_plan_normalization_replaces_git_diff_and_status():
    plan = Plan(goal=Goal(text="inspect"), tasks=[
        PlanTask(order=1, description="status", tool="git_status"),
        PlanTask(order=2, description="diff", tool="git_diff"),
    ])
    normalized = AgentOrchestrator._normalize_plan_for_workspace(plan, tempfile.gettempdir())
    assert [t.tool for t in normalized.tasks] == ["list_directory", "list_directory"]


def test_shell_environment_request_is_not_captured_by_environment_shortcut():
    assert not AgentOrchestrator._is_environment_request("python --version과 pip --version을 셸 명령으로 확인해줘")


def test_project_creation_plan_requires_real_file_mutation():
    plan = Plan(goal=Goal(text="create"), tasks=[
        PlanTask(order=1, description="folder", tool="create_directory"),
    ])
    normalized = AgentOrchestrator._normalize_project_intent_plan(plan, "프로젝트 하나 만들자. 음악 재생기를 만들어줘", tempfile.gettempdir())
    assert any(t.tool == "write_file" for t in normalized.tasks)
