"""Private Telegram identity contract; no corporate service or LLM access."""

import asyncio
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    user_id: str
    display_name: str
    role: Literal["intern", "mentor", "teamlead"]
    team_id: str
    mentor_id: str | None = None


class IdentityError(Exception):
    def __init__(self, kind="unavailable"):
        self.kind = kind
        super().__init__(kind)


class IdentityClient:
    def __init__(self, url: str, key: str, *, transport=None):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid BACKEND_URL")
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"X-Buddy-Key": key},
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def profile(self, user_id: int) -> Profile:
        return await self._request("GET", f"/api/v1/auth/telegram/{user_id}")

    async def bind(self, code: str, user_id: int, chat_id: int) -> Profile:
        return await self._request(
            "POST",
            "/api/v1/auth/bind",
            json={
                "code": code,
                "telegram_user_id": user_id,
                "telegram_chat_id": chat_id,
            },
        )

    async def _request(self, method, path, **kwargs):
        try:
            async with asyncio.timeout(5):
                response = await self.client.request(method, path, **kwargs)
            if response.status_code != 200:
                raise IdentityError(
                    {400: "invalid", 404: "unbound", 409: "conflict"}.get(response.status_code, "unavailable")
                )
            return Profile.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError, TimeoutError):
            raise IdentityError() from None
