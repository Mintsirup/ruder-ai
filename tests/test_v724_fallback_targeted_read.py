from pathlib import Path

from ruder_ai.core.goal_planner import GoalPlanner


class BadLLM:
    async def chat(self, messages):
        return "not json"


class Registry:
    def list_skills(self):
        return {
            "read_file": "read",
            "list_directory": "list",
            "patch_file": "patch",
            "verify_project": "verify",
        }


def test_fallback_uses_explicit_file_before_patch():
    planner = GoalPlanner(BadLLM(), Registry())
    plan = planner._fallback_plan(
        "PlayerController.cs의 실제 컴파일 오류를 찾아서 수정하고, 수정 후 검증까지 해줘",
        warning="parse failed",
    )

    assert [task.tool for task in plan.tasks] == [
        "read_file", "patch_file", "verify_project"
    ]
    assert "PlayerController.cs" in plan.tasks[0].description
    assert "앞서 읽은 실제 파일" in plan.tasks[1].description
    assert "변경 여부와 관계없이" in plan.tasks[2].description


def test_fallback_extracts_multiple_explicit_files():
    planner = GoalPlanner(BadLLM(), Registry())
    names = planner._mentioned_file_names(
        "Assets/Scripts/PlayerController.cs와 CameraFollow.cs를 확인해줘"
    )
    assert names == ["Assets/Scripts/PlayerController.cs", "CameraFollow.cs"]
