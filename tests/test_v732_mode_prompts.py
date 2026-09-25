from ruder_ai.core.agent import AIAgent


def test_mode_prompts_are_distinct():
    assert "STRICT_CODE" in AIAgent.CODING_SYSTEM_PROMPT
    assert "READ_ONLY_INSPECT" in AIAgent.INSPECT_SYSTEM_PROMPT
    assert AIAgent.CODING_SYSTEM_PROMPT != AIAgent.INSPECT_SYSTEM_PROMPT


def test_modelfile_is_coding_only():
    text = __import__("pathlib").Path("Modelfile").read_text(encoding="utf-8")
    assert "CODE MODE only" in text
    assert "Everyday conversation" in text
