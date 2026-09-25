from ruder_ai.core.failure_policy import FailurePolicy
from ruder_ai.verify.unity import is_unity_project


def test_unity_project_detection(tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.22f1\n")
    assert is_unity_project(tmp_path) is True


def test_non_unity_project_detection(tmp_path):
    (tmp_path / "Assets").mkdir()
    assert is_unity_project(tmp_path) is False


def test_failure_policy_replans_deterministic_failures():
    decision = FailurePolicy.decide(error_type="failed", attempt=0, max_retries=0, max_replans=2, replans=0)
    assert decision.action == "replan"


def test_failure_policy_stops_after_replans():
    decision = FailurePolicy.decide(error_type="not_found", attempt=0, max_retries=0, max_replans=2, replans=2)
    assert decision.action == "stop"
