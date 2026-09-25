import pytest

from ruder_ai.core.executor import ToolExecutor


class RetryLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return "수정하겠습니다."
        return (
            '```json\n'
            '{"tool":"patch_file","kwargs":'
            '{"file_path":"Assets/Scripts/PlayerController.cs",'
            '"old":"jumpForce","new":"superJumpForce"}}\n'
            '```'
        )


@pytest.mark.asyncio
async def test_missing_tool_kwargs_get_one_format_retry():
    llm = RetryLLM()
    executor = object.__new__(ToolExecutor)
    executor._last_failure_context = ""
    executor._note_scratchpad = lambda entry: None

    messages = [
        {"role": "user", "content": "patch"},
    ]
    first = await llm.chat(messages)
    kwargs = executor._parse_kwargs_for("patch_file", first)
    assert kwargs == {}

    required = executor._KNOWN_REQUIRED_OVERRIDES["patch_file"]
    assert not all(kwargs.get(key) for key in required)

    messages.append({"role": "assistant", "content": first})
    messages.append({"role": "user", "content": "JSON only"})
    retry = await llm.chat(messages)
    retry_kwargs = executor._parse_kwargs_for("patch_file", retry)
    assert all(retry_kwargs.get(key) for key in required)
    assert retry_kwargs["file_path"].endswith("PlayerController.cs")
