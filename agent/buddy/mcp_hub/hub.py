import asyncio
import re
from collections.abc import Callable

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from buddy.mcp_hub.config import ServerConfig
from buddy.mcp_hub.errors import (
    AuthenticationRequired,
    ConfigurationError,
    InvalidArguments,
    MCPError,
    RemoteToolError,
    SourceUnavailable,
    ToolUnavailable,
)
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.policy import PROBE_TOOLS, is_read_tool, permitted
from buddy.mcp_hub.transport import sdk_session


def public_error(exc):
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            found = public_error(child)
            if found is not None:
                return found
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
        return AuthenticationRequired("MCP authorization is required")
    return None


class Hub:
    def __init__(
        self,
        servers: dict[str, ServerConfig],
        fixtures: FixtureStore,
        *,
        demo_mode=True,
        record_mode=False,
        connector: Callable = sdk_session,
        auth=None,
    ):
        if demo_mode and record_mode:
            raise ConfigurationError("DEMO_MODE and RECORD_MODE are mutually exclusive")
        self.servers = servers
        self.fixtures = fixtures
        secrets = list(fixtures.secrets)
        for config in servers.values():
            secrets.extend(
                value.get_secret_value()
                for value in (config.username, config.password)
                if value.get_secret_value()
            )
            token = config.token.get_secret_value()
            if token:
                secrets.append(token)
            secrets.extend(
                value.get_secret_value()
                for key, value in config.env.items()
                if "TOKEN" in key.upper() and value.get_secret_value()
            )
        self.fixtures.secrets = tuple(set(secrets))
        self.demo_mode = demo_mode
        self.record_mode = record_mode
        self.connector = connector
        self.auth = auth or {}
        self.audit: list[dict] = []

    async def _tools(self, session) -> list[dict]:
        tools, cursors = {}, set()
        cursor = None
        for _ in range(100):
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                value = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                if value["name"] in tools:
                    raise SourceUnavailable("Duplicate tool in discovery")
                tools[value["name"]] = value
            cursor = page.nextCursor
            if not cursor:
                return list(tools.values())
            if cursor in cursors:
                raise SourceUnavailable("Invalid discovery pagination")
            cursors.add(cursor)
        raise SourceUnavailable("Discovery pagination limit exceeded")

    async def discover(self, server: str) -> dict:
        if server not in self.servers:
            raise ConfigurationError("Unknown server")
        if self.demo_mode:
            catalog = self.fixtures.catalog(server)
            return {
                "server": server,
                "status": "fixture",
                "origin": catalog["origin"],
                "tools": catalog["tools"],
                "verified_live": False,
            }
        config = self.servers[server]
        try:
            async with asyncio.timeout(config.timeout):
                async with self.connector(config, auth=self.auth.get(server)) as session:
                    tools = await self._tools(session)
            if self.record_mode:
                self.fixtures.save_catalog(server, tools)
            return {
                "server": server,
                "status": "live",
                "origin": "live",
                "tools": tools,
                "verified_live": True,
            }
        except MCPError:
            raise
        except Exception as exc:
            raise (public_error(exc) or SourceUnavailable("MCP discovery failed")) from None

    @staticmethod
    def _validate(server: str, tool: str, arguments: dict, tools: list[dict]) -> None:
        definition = next((item for item in tools if item.get("name") == tool), None)
        if definition is None or not is_read_tool(server, definition):
            raise ToolUnavailable("Required read tool was not discovered")
        schema = definition.get("inputSchema")
        if not isinstance(schema, dict):
            raise ToolUnavailable("Tool has no valid input schema")
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
        except (ValidationError, SchemaError) as exc:
            raise InvalidArguments("Arguments do not match discovered schema") from exc

    async def call(
        self, case: str, server: str, tool: str, arguments: dict, *, user_id: str = "stage1-probe"
    ) -> dict:
        outcome = "ok"
        try:
            permitted(case, server, tool)
            if server not in self.servers:
                raise ConfigurationError("Unknown server")
            if self.demo_mode:
                catalog = self.fixtures.catalog(server)
                self._validate(server, tool, arguments, catalog["tools"])
                return self.fixtures.load(server, tool, arguments)
            config = self.servers[server]
            async with asyncio.timeout(config.timeout):
                async with self.connector(config, auth=self.auth.get(server)) as session:
                    tools = await self._tools(session)
                    self._validate(server, tool, arguments, tools)
                    result = await session.call_tool(tool, arguments)
                    if result.isError:
                        raise RemoteToolError("MCP tool returned an error")
                    value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if self.record_mode:
                self.fixtures.save_catalog(server, tools)
                self.fixtures.save(server, tool, arguments, value)
            return value
        except MCPError as exc:
            outcome = exc.code
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            error = public_error(exc) or SourceUnavailable("MCP call failed")
            outcome = error.code
            raise error from None
        finally:

            def identifier(value):
                if isinstance(value, str) and any(secret in value for secret in self.fixtures.secrets):
                    return "redacted"
                return (
                    value
                    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)
                    else "invalid"
                )

            self.audit.append(
                {
                    "case": identifier(case),
                    "server": identifier(server),
                    "tool": identifier(tool),
                    "user_id": identifier(user_id),
                    "outcome": outcome,
                }
            )

    async def probe(self) -> list[dict]:
        async def one(server):
            try:
                result = await self.discover(server)
                tools = [tool for tool in result.pop("tools") if is_read_tool(server, tool)]
                result["read_tools"] = [tool["name"] for tool in tools]
                result["required_tool"] = PROBE_TOOLS[server]
                result["required_available"] = PROBE_TOOLS[server] in result["read_tools"]
                return result
            except MCPError as exc:
                return {"server": server, "status": exc.code, "verified_live": False}

        return await asyncio.gather(*(one(server) for server in self.servers))
