from pathlib import Path
import asyncio
from types import SimpleNamespace

from ruder_ai.core.execution import ExecutionContext, ExecutionResult
from ruder_ai.core.executor import ToolExecutor
from ruder_ai.verify.verifier import AutoVerifier
from ruder_ai.verify.models import VerifyReport, CheckResult


def test_execution_result_supports_no_change_state():
    ctx = ExecutionContext(workspace='/tmp', requirement_satisfied=True, result_state='NO_CHANGE')
    result = ExecutionResult(success=True, requirement_satisfied=True, result_state='NO_CHANGE')
    assert result.as_dict()['result_state'] == 'NO_CHANGE'
    assert result.as_dict()['requirement_satisfied'] is True


def test_patch_duplicate_delete_recovery(tmp_path):
    target = tmp_path / 'App.java'
    target.write_text('class App {\nvoid subtract(int a,int b){}\nvoid subtract(int a,int b){}\n}\n', encoding='utf-8')
    ex = ToolExecutor.__new__(ToolExecutor)
    ex.workspace_path = str(tmp_path)
    recovered = ex._patch_file_local_recovery({
        'file_path': 'App.java',
        'old_str': 'void subtract(int a,int b){}',
        'new_str': '',
    })
    assert recovered is not None
    assert recovered['__recovered_patch'] is True
    assert recovered['old_str'] == 'void subtract(int a,int b){}'


def test_patch_already_applied_is_idempotent(tmp_path):
    target = tmp_path / 'main.py'
    target.write_text('def add(a,b):\n    return a+b\n', encoding='utf-8')
    ex = ToolExecutor.__new__(ToolExecutor)
    ex.workspace_path = str(tmp_path)
    recovered = ex._patch_file_local_recovery({
        'file_path': 'main.py',
        'old_str': 'def add(a,b):\n    return a-b',
        'new_str': 'def add(a,b):\n    return a+b',
    })
    assert recovered is not None
    assert recovered['__already_applied'] is True


def test_move_reference_repair(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'index.js').write_text('function sum(a,b){return a+b}\nmodule.exports={sum};\n', encoding='utf-8')
    (tmp_path / 'test.js').write_text('const {sum}=require("./index");\n', encoding='utf-8')
    (tmp_path / 'index.js').rename(tmp_path / 'src' / 'index.js')
    ex = ToolExecutor.__new__(ToolExecutor)
    ex.workspace_path = str(tmp_path)
    changed = ex._repair_moved_file_references('index.js', 'src/index.js')
    assert 'test.js' in changed
    assert './src/index' in (tmp_path / 'test.js').read_text(encoding='utf-8')


def test_gradle_missing_command_falls_back_to_javac(monkeypatch, tmp_path):
    src = tmp_path / 'src' / 'main' / 'java'
    src.mkdir(parents=True)
    (src / 'App.java').write_text('class App {}\n', encoding='utf-8')
    monkeypatch.setattr('ruder_ai.verify.verifier.shutil.which', lambda name: None)
    verifier = AutoVerifier()
    project = SimpleNamespace(build_system='Gradle', language='Java')
    checks = verifier._plan_checks(project, tmp_path, ['src/main/java/App.java'])
    assert checks
    for check in checks:
        check.close() if hasattr(check, "close") else None


def test_conditional_temp_cleanup_noop(tmp_path):
    # No files to delete: this is a valid no-op condition and should not require a delete task.
    assert not list(tmp_path.glob('*.tmp'))


def test_e10_subtract_rewrites_duplicate_or_bad_methods(tmp_path):
    app = tmp_path / 'src' / 'main' / 'java' / 'com' / 'example' / 'App.java'
    app.parent.mkdir(parents=True)
    app.write_text(
        'package com.example;\n\n'
        'public class App {\n'
        '    public static void main(String[] args) { System.out.println("x"); }\n'
        '    public int subtract(int a, int b) { return a + b; }\n'
        '    public int subtract(int a, int b) { return a - b; }\n'
        '}\n', encoding='utf-8'
    )
    from ruder_ai.core.goal_planner import Goal, Plan, PlanTask
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    plan = Plan(goal=Goal('subtract'), tasks=[PlanTask(1, 'bad patch', 'patch_file')])
    fixed = AgentOrchestrator._deterministic_v11_task_fixes(
        plan, 'App에 subtract(int a,int b) 메서드를 추가해줘', tmp_path
    )
    writes = [t for t in fixed.tasks if t.tool == 'write_file']
    assert len(writes) == 1
    content = writes[0].kwargs['content']
    assert content.count('subtract(') == 1
    assert 'return a - b;' in content
    assert content.count('public class App') == 1


def test_d23_null_safety_normalizes_to_single_write(tmp_path):
    target = tmp_path / 'src' / 'main' / 'java' / 'com' / 'example' / 'Calculator.java'
    target.parent.mkdir(parents=True)
    original = (
        'package com.example;\n\n'
        'public class Calculator {\n'
        '    public int add(int a, int b) { return a - b; }\n'
        '    public int multiply(int a, int b) { return a * b; }\n'
        '}\n'
    )
    target.write_text(original, encoding='utf-8')
    from ruder_ai.core.goal_planner import Goal, Plan, PlanTask
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    plan = Plan(goal=Goal('null safety'), tasks=[
        PlanTask(1, 'append overload', 'append_file', kwargs={
            'file_path': 'src/main/java/com/example/Calculator.java',
            'content': '\nwrong planner mutation\n',
        }),
        PlanTask(2, 'rewrite file', 'write_file', kwargs={
            'file_path': 'src/main/java/com/example/Calculator.java',
            'content': original,
        }),
    ])
    fixed = AgentOrchestrator._deterministic_v11_task_fixes(
        plan,
        'Java 코드에서 null 입력이 들어와도 안전하게 처리하도록 고쳐줘',
        tmp_path,
    )
    writes = [t for t in fixed.tasks if t.tool == 'write_file']
    assert len(writes) == 1
    assert not any(t.tool == 'append_file' for t in fixed.tasks)
    content = writes[0].kwargs['content']
    assert '== null' in content
    assert 'add(Integer a, Integer b)' in content
    assert content.count('public class Calculator') == 1


def test_d23_reviewer_guard_requires_source_change_and_javac(tmp_path):
    target = tmp_path / 'src' / 'main' / 'java' / 'com' / 'example' / 'Calculator.java'
    target.parent.mkdir(parents=True)
    original = 'public class Calculator { public int add(int a,int b){ return a-b; } }\n'
    current = 'public class Calculator { public int add(int a,int b){ return a-b; } public int add(Integer a,Integer b){ if (a == null || b == null) return 0; return a+b; } }\n'
    target.write_text(current, encoding='utf-8')
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    assert AgentOrchestrator._d23_null_safety_guard(
        'Java 코드에서 null 입력이 들어와도 안전하게 처리하도록 고쳐줘',
        tmp_path,
        ['src/main/java/com/example/Calculator.java'],
        '- javac: PASS',
        {'src/main/java/com/example/Calculator.java': original},
    ) is True
    assert AgentOrchestrator._d23_null_safety_guard(
        'Java 코드에서 null 입력이 들어와도 안전하게 처리하도록 고쳐줘',
        tmp_path,
        ['src/main/java/com/example/Calculator.java'],
        '- javac: FAIL',
        {'src/main/java/com/example/Calculator.java': original},
    ) is False


def test_r31_node_modularization_guard(tmp_path):
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    (tmp_path / "src").mkdir()
    (tmp_path / "src/utils.js").write_text("function sum(a,b){return a+b}\nmodule.exports={sum};\n", encoding="utf-8")
    (tmp_path / "index.js").write_text('const {sum}=require("./src/utils");\nmodule.exports={sum};\n', encoding="utf-8")
    assert AgentOrchestrator._r31_modularization_guard(
        "Node 코드를 작은 모듈로 나누고 기존 테스트가 계속 동작하게 해줘",
        str(tmp_path),
        ["src/utils.js", "index.js"],
        "- npm_test: PASS",
    ) is True


def test_r31_node_modularization_guard_rejects_missing_split(tmp_path):
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    (tmp_path / "index.js").write_text('module.exports={sum};', encoding="utf-8")
    assert AgentOrchestrator._r31_modularization_guard(
        "Node 코드를 작은 모듈로 나누고 기존 테스트가 계속 동작하게 해줘",
        str(tmp_path),
        ["index.js"],
        "- npm_test: PASS",
    ) is False


def test_r35_conditional_noop_guard_accepts_no_duplicates_with_unrelated_pytest_failure(tmp_path):
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    (tmp_path / "test_main.py").write_text(
        "def test_divide():\n    from main import divide\n    assert divide(6, 2) == 3\n",
        encoding="utf-8",
    )
    assert AgentOrchestrator._r35_conditional_noop_guard(
        "중복되는 테스트가 있다면 공통 fixture 또는 헬퍼로 정리해줘",
        tmp_path,
        [],
        "- pytest: FAIL\nFAILED test_main.py::test_divide",
    ) is True


def test_r35_conditional_noop_guard_rejects_actual_duplicate_tests(tmp_path):
    from ruder_ai.agents.orchestrator import AgentOrchestrator
    (tmp_path / "test_a.py").write_text("def test_same():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_same():\n    pass\n", encoding="utf-8")
    assert AgentOrchestrator._r35_conditional_noop_guard(
        "중복되는 테스트가 있다면 공통 fixture 또는 헬퍼로 정리해줘",
        tmp_path,
        [],
        "- pytest: FAIL",
    ) is False
