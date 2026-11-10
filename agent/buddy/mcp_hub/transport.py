from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from buddy.mcp_hub.config import ServerConfig
from buddy.mcp_hub.errors import ConfigurationError


@asynccontextmanager
async def sdk_session(config: ServerConfig, *, auth: httpx.Auth | None = None):
    if config.transport == "stdio":
        parameters = StdioServerParameters(
            command=config.command,
            args=config.args,
            env={key: value.get_secret_value() for key, value in config.env.items()},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=config.timeout)
            ) as session:
                await session.initialize()
                yield session
    else:
        if not config.url:
            raise ConfigurationError("MCP endpoint is not configured")
        token = config.token.get_secret_value()
        headers = {"Authorization": "Bearer " + token} if token else {}
        if auth is None and config.username.get_secret_value() and not token:
            auth = httpx.BasicAuth(config.username.get_secret_value(), config.password.get_secret_value())
        local = urlsplit(config.url).hostname in {"127.0.0.1", "localhost", "::1"}
        async with httpx.AsyncClient(
            headers=headers, auth=auth, timeout=config.timeout, trust_env=not local
        ) as client:
            async with streamable_http_client(config.url, http_client=client) as (reader, writer, _):
                async with ClientSession(
                    reader, writer, read_timeout_seconds=timedelta(seconds=config.timeout)
                ) as session:
                    await session.initialize()
                    yield session
