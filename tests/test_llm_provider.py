import json
import httpx
import pytest
from app.services.llm_provider import LLMSettings, OpenAIProvider


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "incomplete"])
async def test_installed_sdk_uses_responses_without_hidden_retries(monkeypatch, status):
    import openai

    real_client = openai.AsyncOpenAI
    seen = []
    settings = {}

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1700000000,
            "status": status, "model": "gpt-5-mini",
            "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                        "status": status,
                        "content": [{"type": "output_text", "text": "Qual aplicação você usa?",
                                     "annotations": []}]}],
        })

    def create_client(**kwargs):
        settings.update(kwargs)
        return real_client(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(openai, "AsyncOpenAI", create_client)
    provider = OpenAIProvider(LLMSettings(model="gpt-5-mini", api_key="unit-test-only"))
    try:
        if status == "completed":
            assert await provider.respond("Instrução de teste", [{"role": "user", "content": "Procuro tinta"}]) == "Qual aplicação você usa?"
        else:
            with pytest.raises(RuntimeError, match="incomplete_model_response"):
                await provider.respond("Instrução de teste", [{"role": "user", "content": "Procuro tinta"}])
    finally:
        await provider.aclose()
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/responses"
    body = json.loads(seen[0].content)
    assert body["store"] is False
    assert body["max_output_tokens"] == 1800
    assert settings["max_retries"] == 0
    assert settings["timeout"] == 20.0

