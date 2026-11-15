import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from buddy.config import Settings
from buddy.observability.llm_trace import recorded


class LLM(Protocol):
    async def complete(self, prompt: str, *, fallback: str) -> str: ...


@dataclass
class FakeLLM:
    response: str | None = None
    calls: list[str] = field(default_factory=list)
    # FR-AG: scripted agent steps ({"content": ..., "tool_calls": [...]} or an exception), in order.
    script: list = field(default_factory=list)
    chats: list[list[dict]] = field(default_factory=list)
    choices: list[tuple[str | None, list[str]]] = field(default_factory=list)  # (tool_choice, tool names)

    @recorded("complete", "prompt")
    async def complete(self, prompt: str, *, fallback: str) -> str:
        self.calls.append(prompt)
        return self.response if self.response is not None else fallback

    @recorded("chat", "messages", "tools")
    async def chat(self, messages: list[dict], tools: list[dict], tool_choice: str | None = None) -> dict:
        self.chats.append([dict(message) for message in messages])
        self.choices.append((tool_choice, [tool["function"]["name"] for tool in tools]))
        step = self.script.pop(0) if self.script else {}
        if isinstance(step, BaseException):
            raise step
        return {"content": step.get("content", ""), "tool_calls": list(step.get("tool_calls", []))}


class CompatibleLLM:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    @recorded("complete", "prompt")
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

    @recorded("chat", "messages", "tools")
    async def chat(self, messages: list[dict], tools: list[dict], tool_choice: str | None = None) -> dict:
        """FR-AG: one agent step. Errors propagate: the agent falls back without the LLM (hard rule 3)."""
        base = self.settings.llm_base_url or "https://openrouter.ai/api/v1"
        extra = {}
        if tool_choice:
            # FR-AG-05: a provider that silently ignores tool_choice is not chosen by OpenRouter.
            extra = {
                "tool_choice": tool_choice,
                "provider": {"data_collection": "deny", "require_parameters": True},
            }
        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds, transport=self.transport
        ) as client:
            response = await client.post(
                base.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.llm_api_key.get_secret_value()},
                json={
                    "model": self.settings.llm_model,
                    "messages": messages,
                    "tools": tools,
                    "max_tokens": 800,
                    # The 60 s tail in evals/router/llm-compare.json is attributed to reasoning tokens.
                    "reasoning": {"enabled": False},
                    "provider": {"data_collection": "deny"},
                    **extra,
                },
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
        calls = [
            {"id": call["id"], "name": call["function"]["name"], "arguments": call["function"]["arguments"]}
            for call in message.get("tool_calls") or []
        ]
        content = message.get("content")
        return {"content": content if isinstance(content, str) else "", "tool_calls": calls}


def create_llm(settings: Settings) -> LLM:
    if (settings.demo_mode and not settings.llm_in_demo) or settings.llm_provider == "fake":
        return FakeLLM()
    return CompatibleLLM(settings)
