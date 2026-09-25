from pathlib import Path
import asyncio

from ruder_ai.agents.explorer import ExplorerAgent
from ruder_ai.indexer.models import FileInfo, ProjectIndex, ProjectInfo
from ruder_ai.core.goal_planner import Goal, Plan, PlanTask
from ruder_ai.core.executor import ToolExecutor
from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.skills import SkillRegistry
from ruder_ai.verify.csharp_static import run_csharp_static_check


def test_plan_task_supports_deterministic_kwargs():
    task = PlanTask(1, "read", "read_file", kwargs={"file_path": "Assets/Scripts/A.cs"})
    assert task.kwargs["file_path"].endswith("A.cs")


def test_explorer_reports_runtime_transform_relation(tmp_path: Path):
    a = tmp_path / "Assets/Scripts/PlayerController.cs"
    b = tmp_path / "Assets/Scripts/CameraFollow.cs"
    a.parent.mkdir(parents=True)
    a.write_text("using UnityEngine;\npublic class PlayerController : MonoBehaviour { void Update(){ transform.position += Vector3.forward; } }\n", encoding="utf-8")
    b.write_text("using UnityEngine;\npublic class CameraFollow : MonoBehaviour { [SerializeField] Transform target; void LateUpdate(){ transform.position = target.position; } }\n", encoding="utf-8")
    index = ProjectIndex(
        workspace=tmp_path, project=ProjectInfo(tmp_path, "C#", "", "Unity"),
        files=[
            FileInfo("Assets/Scripts/PlayerController.cs", a, ".cs", a.stat().st_size),
            FileInfo("Assets/Scripts/CameraFollow.cs", b, ".cs", b.stat().st_size),
        ], symbols=[]
    )
    ev = ExplorerAgent(workspace_path=tmp_path).collect_evidence(index, "PlayerController.cs와 CameraFollow.cs 관계 분석")
    assert any("런타임 Transform 의존" in r for r in ev.relations)


def test_csharp_static_nested_target_is_not_skipped(tmp_path: Path):
    p = tmp_path / "Assets/Scripts/PlayerController.cs"
    p.parent.mkdir(parents=True)
    p.write_text("public class PlayerController { private float superJumpForce=25f; void X(){ velocity.y = jumpForce; } }\n", encoding="utf-8")
    result = asyncio.run(run_csharp_static_check(tmp_path, target_files=["PlayerController.cs"]))
    assert result.skipped is False
    assert result.passed is False
    assert "jumpForce" in result.log_text()


def test_environment_skill_is_registered():
    assert "environment_info" in SkillRegistry().list_skills()


def test_deterministic_csharp_repair_plan_uses_exact_source_line(tmp_path: Path):
    p = tmp_path / "Assets/Scripts/PlayerController.cs"
    p.parent.mkdir(parents=True)
    p.write_text("public class PlayerController {\n    private float superJumpForce=25f;\n    void X(){ velocity.y = jumpForce; }\n}\n", encoding="utf-8")
    plan = AgentOrchestrator._deterministic_csharp_repair_plan({
        "status": "failed",
        "summary": "Assets/Scripts/PlayerController.cs: 'jumpForce'가 선언되지 않았습니다. 유사 선언: superJumpForce",
    }, workspace=tmp_path)
    assert plan is not None
    patch = plan.tasks[1]
    assert patch.kwargs["file_path"] == "Assets/Scripts/PlayerController.cs"
    assert patch.kwargs["old_str"] == "    void X(){ velocity.y = jumpForce; }"
    assert patch.kwargs["new_str"] == "    void X(){ velocity.y = superJumpForce; }"
