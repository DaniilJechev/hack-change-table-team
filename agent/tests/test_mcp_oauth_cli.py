import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from buddy.mcp_hub import cli, oauth
from buddy.mcp_hub.errors import ConfigurationError, SourceUnavailable

ROOT = Path(__file__).resolve().parents[2]


async def test_st1_auth_1_env_storage_and_secret_inventory(tmp_path):
    path = tmp_path / ".env"
    storage = oauth.EnvTokenStorage(path)
    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None
    token = OAuthToken(
        access_token="synthetic-access", token_type="Bearer", refresh_token="synthetic-refresh"
    )
    client = OAuthClientInformationFull(
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        redirect_uris=["http://127.0.0.1/callback"],
    )
    await storage.set_tokens(token)
    await storage.set_client_info(client)
    assert (await storage.get_tokens()).access_token == "synthetic-access"
    assert (await storage.get_client_info()).client_id == "synthetic-client"
    assert {"synthetic-access", "synthetic-refresh", "synthetic-secret"}.issubset(
        set(oauth.configured_secrets(path))
    )
    with pytest.raises(ConfigurationError):
        oauth.EnvTokenStorage(tmp_path / "tokens.json")
    path.write_text("MIRO_OAUTH_TOKENS=invalid\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        await storage.get_tokens()
    path.write_text("UNRELATED=ignore\nAPI_KEY=plain-key\nOTHER_SECRET=[]\n", encoding="utf-8")
    assert "plain-key" in oauth.configured_secrets(path)
    assert oauth.configured_secrets(tmp_path / "missing") == ()


async def test_st1_auth_1_noninteractive_requires_explicit_login(tmp_path):
    with pytest.raises(oauth.AuthenticationRequired):
        await oauth.noninteractive_redirect("https://example.invalid/auth")
    with pytest.raises(oauth.AuthenticationRequired):
        await oauth.noninteractive_callback()
    actual = oauth.provider("https://mcp.miro.com", oauth.EnvTokenStorage(tmp_path / ".env"))
    assert isinstance(actual, OAuthClientProvider)


async def test_st1_auth_1_loopback_callback_and_cleanup(tmp_path, monkeypatch):
    def fake_provider(url, storage, **kwargs):
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(oauth, "provider", fake_provider)
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: True)
    async with oauth.browser_flow(
        "https://example.invalid/mcp", oauth.EnvTokenStorage(tmp_path / ".env"), port=0, timeout=1
    ) as auth:
        assert "127.0.0.1" in auth.redirect_uri
        await auth.redirect_handler("https://example.invalid/auth")
        async with httpx.AsyncClient() as client:
            assert (await client.get(auth.redirect_uri + "?error=access_denied")).status_code == 400
            assert (await client.get(auth.redirect_uri + "?state=missing-code")).status_code == 400
            response = await client.get(auth.redirect_uri + "?code=test-code&state=test-state")
            assert response.status_code == 200
            assert await auth.callback_handler() == ("test-code", "test-state")
            assert (await client.get(auth.redirect_uri + "?code=another")).status_code == 409


async def test_st1_auth_1_browser_failure_and_callback_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "provider", lambda url, storage, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: False)
    async with oauth.browser_flow(
        "https://example.invalid/mcp", oauth.EnvTokenStorage(tmp_path / ".env"), port=0, timeout=0.01
    ) as auth:
        with pytest.raises(oauth.AuthenticationRequired):
            await auth.redirect_handler("https://example.invalid/auth")
        with pytest.raises(oauth.AuthenticationRequired):
            await auth.callback_handler()


def test_st1_mcp_1_cli_offline_and_error_codes(tmp_path, capsys):
    common = ["--env-file", str(tmp_path / ".env"), "--fixtures", str(ROOT / "fixtures")]
    assert cli.main(["discover", *common]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "offline"
    assert all(not row["verified_live"] for row in output["servers"])
    assert (
        cli.main(
            [
                "call",
                *common,
                "--server",
                "jira",
                "--tool",
                "jira_get_issue",
                "--arguments",
                '{"issue_key":"ORD-118"}',
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok" and output["recorded"] is False
    assert "ORD-118" not in json.dumps(output)
    for arguments in ["not-json", "[]"]:
        assert cli.main(["call", *common, "--arguments", arguments]) == 1
        assert json.loads(capsys.readouterr().out)["status"] == "configuration_error"
    assert cli.main(["auth-miro", *common]) == 2
    capsys.readouterr()
    assert cli.main(["discover", *common, "--record"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "configuration_error"
    assert cli.main(["discover", "--fixtures", str(tmp_path / "missing")]) == 1
    # Four stage-1 sources plus the H4 PowerPoint server.
    assert len(json.loads(capsys.readouterr().out)["servers"]) == 5


@pytest.mark.parametrize(
    "error", [OSError("hidden-error"), TimeoutError(), SourceUnavailable("hidden-error")]
)
def test_st1_mcp_1_cli_sanitizes_operational_errors(monkeypatch, capsys, error):
    async def failed(args):
        raise error

    monkeypatch.setattr(cli, "execute", failed)
    assert cli.main(["discover"]) == 1
    output = capsys.readouterr().out
    assert "hidden-error" not in output
    assert json.loads(output)["status"] == "source_unavailable"


async def test_st1_auth_1_cli_explicit_authorization_path(tmp_path, monkeypatch):
    class FakeHub:
        servers = {"miro": SimpleNamespace(url="https://example.invalid/mcp")}
        auth = {}
        demo_mode = True

        async def discover(self, server):
            assert self.demo_mode is False and self.auth["miro"] == "test-auth"
            return {"tools": [1, 2]}

    hub = FakeHub()

    @asynccontextmanager
    async def flow(*args, **kwargs):
        yield "test-auth"

    monkeypatch.setattr(cli, "build_hub", lambda args: hub)
    monkeypatch.setattr(cli, "browser_flow", flow)
    result, code = await cli.execute(SimpleNamespace(action="auth-miro", env_file=tmp_path / ".env", port=0))
    assert result["status"] == "authorized" and code == 0


def test_st1_auth_1_live_config_uses_oauth_only_for_miro(tmp_path):
    args = SimpleNamespace(
        env_file=tmp_path / ".env", live=True, record=False, fixtures=tmp_path / "fixtures", step="step_1"
    )
    hub = cli.build_hub(args)
    assert hub.demo_mode is False and isinstance(hub.auth["miro"], OAuthClientProvider)
