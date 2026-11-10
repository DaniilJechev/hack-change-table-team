import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from buddy.mcp_hub.errors import FixtureInvalid, FixtureMissing, SecretInFixture

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
SECRET_KEYS = {"access_token", "refresh_token", "authorization", "password", "client_secret", "api_key"}


def canonical(arguments: dict) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def key_for(server: str, tool: str, arguments: dict) -> str:
    return hashlib.sha256((server + "/" + tool + "/" + canonical(arguments)).encode()).hexdigest()


class FixtureStore:
    def __init__(self, root: Path, *, step: str = "step_1", secrets: tuple[str, ...] = ()):
        self.root = root.resolve()
        self.step = step
        self.secrets = tuple(secret for secret in secrets if secret)
        if not IDENTIFIER.fullmatch(step):
            raise FixtureInvalid("Invalid fixture step")

    def _path(self, server: str, filename: str) -> Path:
        if not IDENTIFIER.fullmatch(server):
            raise FixtureInvalid("Invalid server name")
        path = self.root / self.step / server / filename
        if not path.resolve().is_relative_to(self.root):
            raise FixtureInvalid("Fixture path leaves its root")
        return path

    def _check(self, value) -> None:
        def inspect(data):
            if isinstance(data, dict):
                if any(str(k).lower() in SECRET_KEYS and v for k, v in data.items()):
                    raise SecretInFixture("Secret field in fixture data")
                for child in data.values():
                    inspect(child)
            elif isinstance(data, list):
                for child in data:
                    inspect(child)
            elif isinstance(data, str):
                if any(secret in data for secret in self.secrets):
                    raise SecretInFixture("Configured secret in fixture data")
                # Also inspect JSON encoded inside MCP TextContent.
                try:
                    decoded = json.loads(data)
                except (ValueError, TypeError):
                    return
                if isinstance(decoded, (dict, list)):
                    inspect(decoded)

        inspect(value)

    def _write(self, path: Path, value: dict) -> None:
        self._check(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _read(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FixtureMissing("No fixture for these arguments and step") from exc
        except (ValueError, OSError) as exc:
            raise FixtureInvalid("Invalid fixture JSON") from exc
        if not isinstance(value, dict) or value.get("origin") not in ("synthetic", "recorded"):
            raise FixtureInvalid("Fixture provenance is missing")
        self._check(value)
        return value

    def save_catalog(self, server: str, tools: list[dict], *, origin: str = "recorded") -> None:
        if origin not in ("synthetic", "recorded"):
            raise FixtureInvalid("Unknown origin")
        self._write(
            self._path(server, "catalog.json"),
            {
                "server": server,
                "step": self.step,
                "origin": origin,
                "captured_at": datetime.now(UTC).isoformat(),
                "tools": tools,
            },
        )

    def catalog(self, server: str) -> dict:
        value = self._read(self._path(server, "catalog.json"))
        if (
            value.get("server") != server
            or value.get("step") != self.step
            or not isinstance(value.get("tools"), list)
        ):
            raise FixtureInvalid("Catalog identity does not match")
        for tool in value["tools"]:
            if (
                not isinstance(tool, dict)
                or not isinstance(tool.get("name"), str)
                or not isinstance(tool.get("inputSchema"), dict)
                or (tool.get("annotations") is not None and not isinstance(tool["annotations"], dict))
            ):
                raise FixtureInvalid("Invalid tool definition in fixture catalog")
        return value

    def save(
        self, server: str, tool: str, arguments: dict, result: dict, *, origin: str = "recorded"
    ) -> None:
        if origin not in ("synthetic", "recorded"):
            raise FixtureInvalid("Unknown origin")
        if result.get("isError"):
            raise FixtureInvalid("MCP errors cannot be recorded as successful fixtures")
        self._write(
            self._path(server, key_for(server, tool, arguments) + ".json"),
            {
                "server": server,
                "tool": tool,
                "arguments": arguments,
                "step": self.step,
                "origin": origin,
                "captured_at": datetime.now(UTC).isoformat(),
                "result": result,
            },
        )

    def load(self, server: str, tool: str, arguments: dict) -> dict:
        value = self._read(self._path(server, key_for(server, tool, arguments) + ".json"))
        if (
            (value.get("server"), value.get("tool"), value.get("arguments"), value.get("step"))
            != (
                server,
                tool,
                arguments,
                self.step,
            )
            or not isinstance(value.get("result"), dict)
            or value["result"].get("isError")
        ):
            raise FixtureInvalid("Fixture identity or result does not match")
        return value["result"]
