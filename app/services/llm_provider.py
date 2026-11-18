"""OpenAI adapter for staged Bruno SDR migration.

Not wired to production. Existing Anthropic flow remains unchanged.
"""
import os
from dataclasses import dataclass
from typing import Sequence, Mapping


@dataclass(frozen=True)
class LLMSettings:
    model: str
    api_key: str
    max_output_tokens: int = 650

    @classmethod
    def from_environment(cls):
        key = os.getenv("OPENAI_API_KEY", "")
        model = os.getenv("BRUNO_OPENAI_MODEL", "gpt-5-mini")
        if not key or key == "stub":
            raise RuntimeError("openai_api_key_missing")
        if not model.startswith("gpt-"):
            raise ValueError("unsupported_model_configuration")
        return cls(model=model, api_key=key)


class OpenAIProvider:
    def __init__(self, settings: LLMSettings):
        from openai import AsyncOpenAI
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.api_key, timeout=45.0, max_retries=1)

    async def respond(self, instructions: str, messages: Sequence[Mapping[str, str]]) -> str:
        if not instructions.strip():
            raise ValueError("missing_instructions")
        clean = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                raise ValueError("invalid_message")
            clean.append({"role": role, "content": content})
        if not clean:
            raise ValueError("empty_history")
        response = await self.client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=clean,
            max_output_tokens=self.settings.max_output_tokens,
            store=False,
        )
        output = (response.output_text or "").strip()
        if not output:
            raise RuntimeError("empty_model_response")
        return output
