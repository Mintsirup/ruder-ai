from ruder_ai.core.agent import AIAgent


def test_mode_prompts_are_distinct():
    assert "STRICT_CODE" in AIAgent.CODING_SYSTEM_PROMPT
    assert "READ_ONLY_INSPECT" in AIAgent.INSPECT_SYSTEM_PROMPT
    assert AIAgent.CODING_SYSTEM_PROMPT != AIAgent.INSPECT_SYSTEM_PROMPT


def test_modelfile_is_coding_only():
    from pathlib import Path

    modelfiles = sorted(Path("Modelfiles").glob("ruder-ai-*"))
    assert modelfiles, "no Ollama Modelfiles found under Modelfiles/"
    for modelfile in modelfiles:
        text = modelfile.read_text(encoding="utf-8")
        assert "CODE MODE only" in text, modelfile.name
        assert "Everyday conversation" in text, modelfile.name
