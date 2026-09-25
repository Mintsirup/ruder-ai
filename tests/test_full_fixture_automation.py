from pathlib import Path


def test_project_ai_fixture_bundle_is_complete():
    root = Path(__file__).resolve().parent / "fixtures" / "project_ai" / "TEST_MATERIALS"
    assert (root / "README.md").is_file()
    assert (root / "expected-results.md").is_file()
    assert (root / "test-prompts-ko.md").is_file()
