import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from buddy.mcp_hub.errors import ConfigurationError

ServerName = Literal["github", "jira", "calendar", "miro"]
SERVERS = ("github", "jira", "calendar", "miro")


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: ServerName
    transport: Literal["stdio", "http"] = "http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    token: SecretStr = SecretStr("")
    timeout: float = Field(default=15, gt=0, le=60)
    oauth: bool = False

    @model_validator(mode="after")
    def check_endpoint(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio requires a command")
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid MCP URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("credentials, query and fragments are forbidden in MCP URLs")
        return self


def load_servers(env_file: Path) -> dict[str, ServerConfig]:
    # Deliberately read only the explicit local file, not unrelated account credentials.
    values = dotenv_values(env_file) if env_file.exists() else {}
    result = {}
    prefixes = {"github": "GITHUB", "jira": "JIRA", "calendar": "GCAL", "miro": "MIRO"}
    for name, prefix in prefixes.items():
        kwargs = {"name": name, "url": values.get(prefix + "_MCP_URL") or ""}
        token = values.get(prefix + "_MCP_TOKEN") or ""
        kwargs["token"] = SecretStr(token)
        command = values.get(prefix + "_MCP_COMMAND")
        if command:
            try:
                args = json.loads(values.get(prefix + "_MCP_ARGS") or "[]")
            except (TypeError, ValueError) as exc:
                raise ConfigurationError("Invalid MCP command arguments") from exc
            kwargs.update(transport="stdio", command=command, args=args)
            if name == "github":
                kwargs["env"] = {
                    "GITHUB_READ_ONLY": SecretStr("1"),
                    "GITHUB_PERSONAL_ACCESS_TOKEN": SecretStr(
                        values.get("GITHUB_PERSONAL_ACCESS_TOKEN") or ""
                    ),
                }
        if name == "miro":
            kwargs["url"] = kwargs["url"] or "https://mcp.miro.com"
            kwargs["oauth"] = not bool(token)
        try:
            result[name] = ServerConfig(**kwargs)
        except ValueError as exc:
            raise ConfigurationError("Invalid MCP configuration for " + name) from exc
    return result
