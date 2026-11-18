import pytest
from app.services.llm_provider import LLMSettings


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="openai_api_key_missing"):
        LLMSettings.from_environment()


def test_valid_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("BRUNO_OPENAI_MODEL", "gpt-5-mini")
    settings = LLMSettings.from_environment()
    assert settings.model == "gpt-5-mini"


def test_invalid_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("BRUNO_OPENAI_MODEL", "claude-sonnet")
    with pytest.raises(ValueError, match="unsupported_model_configuration"):
        LLMSettings.from_environment()
