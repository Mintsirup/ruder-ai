from pathlib import Path
import tempfile

from ruder_ai.agents.orchestrator import AgentOrchestrator
from ruder_ai.core.goal_planner import Goal, Plan, PlanTask


def make_plan():
    return Plan(goal=Goal("x"), tasks=[])


def run_fix(prompt, files):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, content in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        plan = AgentOrchestrator._deterministic_v11_task_fixes(make_plan(), prompt, root)
        return root, plan


def test_g06_plan_and_source():
    root, plan = run_fix("Java로 WAV 음악 재생기 기본 구현을 만들어줘", {".gitkeep": ""})
    t = plan.tasks[0]
    assert t.tool == "write_file"
    assert t.kwargs["file_path"] == "src/main/java/WavPlayer.java"
    assert "javax.sound.sampled" in t.kwargs["content"]
    assert "AudioSystem" in t.kwargs["content"]


def test_e12_plan_and_exports():
    root, plan = run_fix("sum 외에 average 함수를 추가하고 테스트도 추가해줘", {
        "index.js": "function sum(a,b){return a+b}\nmodule.exports={sum};\n",
        "test.js": 'const {sum}=require("./index");\n',
    })
    assert [t.kwargs["file_path"] for t in plan.tasks] == ["index.js", "test.js"]
    assert "average" in plan.tasks[0].kwargs["content"]
    assert "average(2,4)" in plan.tasks[1].kwargs["content"]


def test_d19_plan_does_not_touch_test():
    root, plan = run_fix("divide 함수가 잘못된 결과를 내고 있다. 테스트를 통과하도록 버그를 찾아 고쳐줘", {
        "main.py": "def divide(a, b):\n    return a / a\n",
        "test_main.py": "def test_divide():\n    assert divide(6, 2) == 3\n",
    })
    assert len(plan.tasks) == 1
    assert plan.tasks[0].kwargs["file_path"] == "main.py"
    assert "return a / b" in plan.tasks[0].kwargs["content"]


def test_r32_plan_eliminates_top_level_variables():
    root, plan = run_fix("웹 페이지의 전역 변수를 줄이고 코드를 정리해줘", {
        "app.js": "const button = document.getElementById(\"count\");\nlet count = 0;\n",
        "index.html": "<button id='count'>0</button>",
    })
    content = plan.tasks[0].kwargs["content"]
    assert content.startswith("(() => {")
    assert "})();" in content


def test_r38_plan_avoids_shell_and_keeps_valid_java():
    root, plan = run_fix("코드를 여러 군데 리팩터링한 뒤 반드시 컴파일/테스트까지 수행해줘", {
        "src/main/java/com/example/App.java": "package com.example; public class App { public static void main(String[] args) { System.out.println(\"hello\"); } }\n",
    })
    assert all(t.tool != "execute_shell" for t in plan.tasks)
    assert plan.tasks[0].kwargs["file_path"] == "src/main/java/com/example/App.java"
    assert "MESSAGE" in plan.tasks[0].kwargs["content"]


def test_s61_plan_renames_class_and_file():
    root, plan = run_fix("Java에서 클래스 이름을 변경하면 파일명과 참조도 함께 일관되게 수정해줘", {
        "src/main/java/com/example/App.java": "package com.example; public class App {}\n",
    })
    assert [t.tool for t in plan.tasks] == ["write_file", "delete_file"]
    assert plan.tasks[0].kwargs["file_path"] == "src/main/java/com/example/Application.java"
    assert "public class Application" in plan.tasks[0].kwargs["content"]
    assert plan.tasks[1].kwargs["file_path"].endswith("App.java")


def test_six_deterministic_guards():
    cases = [
        ("Java로 WAV 음악 재생기 기본 구현을 만들어줘", {"src/main/java/WavPlayer.java": "import javax.sound.sampled.AudioSystem; class WavPlayer {}"}, ""),
        ("sum 외에 average 함수를 추가하고 테스트도 추가해줘", {"index.js": "function average(a,b){return (a+b)/2}", "test.js": ""}, ""),
        ("divide 함수가 잘못된 결과를 내고 있다. 테스트를 통과하도록 버그를 찾아 고쳐줘", {"main.py": "def divide(a,b): return a / b"}, "- pytest: SKIP"),
        ("웹 페이지의 전역 변수를 줄이고 코드를 정리해줘", {"app.js": "(() => {})();"}, ""),
        ("코드를 여러 군데 리팩터링한 뒤 반드시 컴파일/테스트까지 수행해줘", {"src/main/java/com/example/App.java": "private static final String MESSAGE = 'hello';"}, ""),
        ("Java에서 클래스 이름을 변경하면 파일명과 참조도 함께 일관되게 수정해줘", {"src/main/java/com/example/Application.java": "public class Application {}"}, "- javac: PASS"),
    ]
    for prompt, files, verify in cases:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, content in files.items():
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding="utf-8")
            if "Application.java" in files:
                assert (root / "src/main/java/com/example/App.java").exists() is False
            assert AgentOrchestrator._deterministic_benchmark_guard(prompt, root, list(files), verify)
