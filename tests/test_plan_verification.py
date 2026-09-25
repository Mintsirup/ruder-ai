from ruder_ai.core.goal_planner import Goal, GoalPlanner, PlanTask, Plan


class Registry:
    def list_skills(self):
        return {
            "patch_file": "patch",
            "verify_project": "verify",
        }


def test_mutation_plan_gets_verify_step():
    planner = GoalPlanner(llm=None, skill_registry=Registry())
    plan = Plan(
        goal=Goal("수정"),
        tasks=[PlanTask(1, "기존 코드 수정", "patch_file")],
    )
    out = planner._ensure_mutation_verification(plan)
    assert [t.tool for t in out.tasks] == ["patch_file", "verify_project"]


def test_non_mutating_plan_is_unchanged():
    planner = GoalPlanner(llm=None, skill_registry=Registry())
    plan = Plan(goal=Goal("읽기"), tasks=[PlanTask(1, "읽기", "read_file")])
    out = planner._ensure_mutation_verification(plan)
    assert [t.tool for t in out.tasks] == ["read_file"]
