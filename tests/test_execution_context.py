from ruder_ai.core.execution import ExecutionContext, ExecutionResult


def test_execution_context_tracks_single_truth():
    ctx = ExecutionContext(request="fix", workspace="/tmp/ws", role="Coder")
    rec = ctx.record_task(1, "patch_file", {"file_path": "A.cs"})
    rec.status = "succeeded"
    ctx.add_changed_file("A.cs")
    ctx.verification = {"success": True, "summary": "PASS"}
    data = ctx.as_dict()
    assert data["changed_files"] == ["A.cs"]
    assert data["tasks"][0]["status"] == "succeeded"
    assert data["verification"]["success"] is True


def test_execution_result_is_structured():
    result = ExecutionResult(success=True, changed_files=["A.cs"], verification={"success": True})
    assert result.as_dict()["success"] is True
    assert result.as_dict()["changed_files"] == ["A.cs"]


def test_execution_context_can_render_result_without_llm():
    ctx = ExecutionContext(request="verify", workspace="/tmp/ws")
    ctx.add_changed_file("Assets/Scripts/A.cs")
    ctx.verification = {"success": True}
    result = ExecutionResult(success=True, changed_files=ctx.changed_files, verification=ctx.verification)
    assert result.as_dict()["verification"]["success"] is True
