import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User

from buddy.bot.handlers import create_dispatcher
from buddy.bot.identity import IdentityClient
from buddy.mcp_hub.config import load_servers
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.hub import Hub
from buddy.smoke import OfflineSession

PROFILE = {
    "user_id": "demo-daniil",
    "display_name": "Синтетический стажёр",
    "team_id": "demo-orders",
    "role": "intern",
}


async def dispatch(client, text="/start", *, group=False, missing=False, bot_user=False):
    session = OfflineSession()
    bot = Bot(token="123456:OFFLINE_TEST_TOKEN", session=session)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-99 if group else 42, type="group" if group else "private"),
        from_user=None if missing else User(id=42, is_bot=bot_user, first_name="Test"),
        text=text,
    )
    try:
        await create_dispatcher(client).feed_update(bot, Update(update_id=1, message=message))
        return session.sent[-1].text
    finally:
        await bot.session.close()


@pytest.mark.parametrize("role", ["intern", "mentor", "teamlead"])
async def test_st1_bind_1_success_and_trusted_ids(role):
    def handle(request):
        assert request.headers["X-Buddy-Key"] == "local-secret"
        assert json.loads(request.content) == {
            "code": "invite-only",
            "telegram_user_id": 42,
            "telegram_chat_id": 42,
        }
        return httpx.Response(200, json={**PROFILE, "role": role})

    client = IdentityClient("http://backend", "local-secret", transport=httpx.MockTransport(handle))
    try:
        text = await dispatch(client, "/start invite-only")
        assert "demo-orders" in text and "invite-only" not in text and "local-secret" not in text
    finally:
        await client.close()


async def test_st1_bind_1_repeat_and_help():
    def handle(request):
        assert request.method == "GET" and request.url.path.endswith("/42")
        return httpx.Response(200, json=PROFILE)

    client = IdentityClient("http://backend", "key", transport=httpx.MockTransport(handle))
    try:
        assert "стажёр" in await dispatch(client)
        assert "Этап 1" in await dispatch(client, "/help")
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "недействителен"),
        (404, "приглашения"),
        (409, "другим"),
        (401, "недоступен"),
        (503, "недоступен"),
        (302, "недоступен"),
    ],
)
async def test_st1_bind_1_safe_errors(status, expected):
    client = IdentityClient(
        "http://backend",
        "hidden-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="hidden-error", headers={"Location": "http://other"})
        ),
    )
    try:
        text = await dispatch(client, "/start hidden-invite")
        assert expected in text
        assert not any(secret in text for secret in ("hidden-key", "hidden-error", "hidden-invite"))
    finally:
        await client.close()


@pytest.mark.parametrize("kind", ["malformed", "profile", "timeout", "connection"])
async def test_st1_bind_1_bad_response(kind):
    def handle(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("hidden")
        if kind == "connection":
            raise httpx.ConnectError("hidden")
        return httpx.Response(200, text="bad" if kind == "malformed" else "{}")

    client = IdentityClient("http://backend", "key", transport=httpx.MockTransport(handle))
    try:
        assert "недоступен" in await dispatch(client)
    finally:
        await client.close()


@pytest.mark.parametrize("kwargs", [{"group": True}, {"missing": True}, {"bot_user": True}])
async def test_st1_bind_1_private_only(kwargs):
    client = AsyncMock()
    assert "личный чат" in await dispatch(client, "/start secret", **kwargs)
    client.bind.assert_not_awaited()
    client.profile.assert_not_awaited()


@pytest.mark.parametrize("url", ["file:///tmp", "http://user:password@backend", "http://backend?key=bad"])
def test_st1_bind_1_endpoint_validation(url):
    with pytest.raises(ValueError, match="BACKEND_URL"):
        IdentityClient(url, "key")


def test_st1_mcp_compat_1_environment_and_redaction(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("JIRA_USERNAME=file-user\n", encoding="utf-8")
    monkeypatch.setenv("MCP_ATLASSIAN_URL", "http://jira:8000/mcp/")
    monkeypatch.setenv("MCP_CALENDAR_URL", "http://calendar:3000/mcp")
    monkeypatch.setenv("JIRA_USERNAME", "process-user")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret-pass")
    configs = load_servers(path)
    assert configs["jira"].url == "http://jira:8000/mcp/"
    assert configs["calendar"].url == "http://calendar:3000/mcp"
    assert configs["jira"].username.get_secret_value() == "process-user"
    assert "secret-pass" not in repr(configs)
    store = FixtureStore(tmp_path / "fixtures")
    Hub(configs, store)
    assert "secret-pass" in store.secrets


@pytest.mark.parametrize("kwargs", [{"backend_url": "http://backend"}, {"buddy_service_key": "hidden"}])
def test_st1_bind_1_partial_configuration(kwargs):
    from buddy.config import Settings

    with pytest.raises(ValueError, match="configured together"):
        Settings(_env_file=None, **kwargs)


def test_st1_mcp_compat_1_container_wrapper(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "is_file", lambda path: str(path).replace("\\", "/") == "/app/github_mcp.py")
    config = load_servers(tmp_path / ".env")["github"]
    assert config.transport == "stdio" and config.args == ["/app/github_mcp.py"]
    assert config.env["GITHUB_READ_ONLY"].get_secret_value() == "1"
