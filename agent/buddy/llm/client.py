import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from buddy.config import Settings


class LLM(Protocol):
    async def complete(self, prompt: str, *, fallback: str) -> str: ...


@dataclass
class FakeLLM:
    response: str | None = None
    calls: list[str] = field(default_factory=list)

    async def complete(self, prompt: str, *, fallback: str) -> str:
        self.calls.append(prompt)
        return self.response if self.response is not None else fallback


class CompatibleLLM:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def complete(self, prompt: str, *, fallback: str) -> str:
        base = self.settings.llm_base_url or "https://openrouter.ai/api/v1"
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self.settings.llm_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.post(
                        base.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.settings.llm_api_key.get_secret_value()},
                        json={
                            "model": self.settings.llm_model,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    response.raise_for_status()
                    text = response.json()["choices"][0]["message"]["content"]
                    return text if isinstance(text, str) and text.strip() else fallback
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
            return fallback


def create_llm(settings: Settings) -> LLM:
    if (settings.demo_mode and not settings.llm_in_demo) or settings.llm_provider == "fake":
        return FakeLLM()
    return CompatibleLLM(settings)
