import asyncio
import json
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from aiohttp import web
from dotenv import dotenv_values, set_key
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import ValidationError

from buddy.mcp_hub.errors import AuthenticationRequired, ConfigurationError


class EnvTokenStorage:
    def __init__(self, path: Path):
        if path.name != ".env":
            raise ConfigurationError("OAuth credentials must be stored in .env")
        self.path = path

    def _get(self, key, model):
        raw = dotenv_values(self.path, interpolate=False).get(key) if self.path.exists() else None
        if not raw:
            return None
        try:
            return model.model_validate_json(raw)
        except (ValueError, ValidationError) as exc:
            raise ConfigurationError("Invalid cached OAuth state") from exc

    def _set(self, key: str, value) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        set_key(str(self.path), key, value.model_dump_json(exclude_none=True), quote_mode="always")

    async def get_tokens(self) -> OAuthToken | None:
        return self._get("MIRO_OAUTH_TOKENS", OAuthToken)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._set("MIRO_OAUTH_TOKENS", tokens)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._get("MIRO_OAUTH_CLIENT", OAuthClientInformationFull)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._set("MIRO_OAUTH_CLIENT", client_info)


async def noninteractive_redirect(url: str) -> None:
    raise AuthenticationRequired("Run the explicit Miro OAuth command first")


async def noninteractive_callback() -> tuple[str, str | None]:
    raise AuthenticationRequired("Interactive authorization has not been completed")


def provider(
    url: str,
    storage: EnvTokenStorage,
    *,
    redirect_uri="http://127.0.0.1:8765/callback",
    redirect_handler=noninteractive_redirect,
    callback_handler=noninteractive_callback,
):
    metadata = OAuthClientMetadata(
        client_name="AI-buddy read-only MCP client",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    return OAuthClientProvider(
        url, metadata, storage, redirect_handler=redirect_handler, callback_handler=callback_handler
    )


@asynccontextmanager
async def browser_flow(url: str, storage: EnvTokenStorage, *, port: int = 8765, timeout: float = 180):
    future = asyncio.get_running_loop().create_future()

    async def callback(request):
        if future.done():
            return web.Response(status=409, text="Authorization callback already received")
        if request.query.get("error") or not request.query.get("code"):
            return web.Response(status=400, text="Authorization was not completed")
        # SDK compares state and exchanges the PKCE code; never log query strings or codes.
        future.set_result((request.query["code"], request.query.get("state")))
        return web.Response(text="Authorization received. Return to the terminal.")

    app = web.Application()
    app.router.add_get("/callback", callback)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        actual_port = site._server.sockets[0].getsockname()[1]

        async def redirect(location):
            if not webbrowser.open(location):
                raise AuthenticationRequired("Browser could not be opened")

        async def wait_callback():
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except TimeoutError as exc:
                raise AuthenticationRequired("OAuth callback timed out") from exc

        yield provider(
            url,
            storage,
            redirect_uri=f"http://127.0.0.1:{actual_port}/callback",
            redirect_handler=redirect,
            callback_handler=wait_callback,
        )
    finally:
        if not future.done():
            future.cancel()
        await runner.cleanup()


def configured_secrets(env_file: Path) -> tuple[str, ...]:
    values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
    secrets = []
    for key, value in values.items():
        if not value or not (
            key == "MIRO_OAUTH_CLIENT"
            or any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
        ):
            continue
        secrets.append(value)
        try:
            data = json.loads(value)
        except ValueError:
            continue
        if isinstance(data, dict):
            for name in ("access_token", "refresh_token", "client_secret"):
                if isinstance(data.get(name), str) and data[name]:
                    secrets.append(data[name])
    return tuple(secrets)
