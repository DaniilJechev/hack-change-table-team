import asyncio
import json
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.types import CallToolResult, TextContent, Tool

from buddy.mcp_hub.config import ServerConfig, load_servers
from buddy.mcp_hub.errors import (
    ConfigurationError,
    FixtureInvalid,
    FixtureMissing,
    InvalidArguments,
    RemoteToolError,
    SecretInFixture,
    SourceUnavailable,
    ToolDenied,
    ToolUnavailable,
)
from buddy.mcp_hub.fixtures import FixtureStore, key_for
from buddy.mcp_hub.hub import Hub
from buddy.mcp_hub.policy import PROBE_TOOLS
from buddy.mcp_hub.transport import sdk_session

ROOT = Path(__file__).resolve().parents[2]
SERVER_SCRIPT = Path(__file__).with_name("mcp_server_fixture.py")
SCENARIO = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))


def configs(**kwargs):
    # The four stage-1 sources; the H4 PowerPoint server has its own tests (test_h4_equator.py).
    return {name: ServerConfig(name=name, **kwargs) for name in PROBE_TOOLS if name != "powerpoint"}


class FakeSession:
    def __init__(self, tools=None, result=None):
        self.tools = tools or []
        self.result = result or CallToolResult(content=[TextContent(type="text", text="synthetic")])
        self.calls = []

    async def list_tools(self, cursor=None):
        return SimpleNamespace(tools=self.tools, nextCursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def tool(name="jira_get_issue", *, read=True, schema=None):
    return Tool(
        name=name,
        inputSchema=schema
        if schema is not None
        else {
            "type": "object",
            "properties": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": read},
    )


def fake_connector(session):
    @asynccontextmanager
    async def connect(config, **kwargs):
        yield session

    return connect


def test_st1_mcp_1_configuration(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'GITHUB_MCP_COMMAND=python\nGITHUB_MCP_ARGS=["-m","synthetic"]\n'
        "GITHUB_PERSONAL_ACCESS_TOKEN=secret-value\nJIRA_MCP_URL=http://127.0.0.1:8769/mcp\n",
        encoding="utf-8",
    )
    result = load_servers(env)
    assert set(result) == set(PROBE_TOOLS)
    assert result["github"].transport == "stdio"
    assert result["github"].env["GITHUB_READ_ONLY"].get_secret_value() == "1"
    assert "secret-value" not in repr(result["github"])
    assert result["miro"].oauth is True
    env.write_text("GITHUB_MCP_COMMAND=x\nGITHUB_MCP_ARGS=invalid\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_servers(env)
    env.write_text("JIRA_MCP_URL=https://user:password@example.invalid/mcp\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_servers(env)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"transport": "stdio"},
        {"url": "ftp://example.invalid"},
        {"url": "https://example.invalid/mcp?token=secret"},
        {"timeout": 0},
    ],
)
def test_st1_mcp_1_invalid_endpoint(kwargs):
    with pytest.raises(ValueError):
        ServerConfig(name="jira", **kwargs)


async def test_st1_fix_1_offline_all_four_sources_and_steps():
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Offline mode tried a network connection")

    for step in ["step_1", "step_2"]:
        hub = Hub(configs(), FixtureStore(ROOT / "fixtures", step=step), connector=network_forbidden)
        reports = await hub.probe()
        assert all(report["required_available"] and not report["verified_live"] for report in reports)
        assert all(report["status"] == "fixture" and report["origin"] == "synthetic" for report in reports)
        for server, sample in SCENARIO["calls"].items():
            result = await hub.call("stage1_probe", server, sample["tool"], sample["arguments"])
            body = json.loads(result["content"][0]["text"])
            assert body["synthetic"] is True
            if server == "miro":
                assert (any(r["user_id"] == "demo-daniil" for r in body["roles"])) == (step == "step_2")
        assert len(hub.audit) == 4 and all(row["outcome"] == "ok" for row in hub.audit)


async def test_st1_ro_1_write_and_case_blocked_before_network(tmp_path):
    session = FakeSession([tool("board_share", read=False)])
    hub = Hub(configs(), FixtureStore(tmp_path), demo_mode=False, connector=fake_connector(session))
    for case, server, name in [
        ("stage1_probe", "miro", "board_share"),
        ("unknown_case", "jira", "jira_get_issue"),
    ]:
        with pytest.raises(ToolDenied):
            await hub.call(case, server, name, {})
    assert not session.calls
    assert all(row["outcome"] == "tool_denied" for row in hub.audit)


@pytest.mark.parametrize(
    "tools,error",
    [
        ([], ToolUnavailable),
        ([tool(read=False)], ToolUnavailable),
        ([tool()], InvalidArguments),
        ([tool(schema={"type": "not_a_json_type"})], InvalidArguments),
    ],
)
async def test_st1_ro_1_discovery_and_schema_enforced(tmp_path, tools, error):
    session = FakeSession(tools)
    hub = Hub(configs(), FixtureStore(tmp_path), demo_mode=False, connector=fake_connector(session))
    with pytest.raises(error):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"wrong": "secret-argument"})
    assert not session.calls
    assert "secret-argument" not in json.dumps(hub.audit)


async def test_st1_fix_1_record_replay_and_no_args_in_audit(tmp_path):
    session = FakeSession([tool()])
    store = FixtureStore(tmp_path)
    hub = Hub(configs(), store, demo_mode=False, record_mode=True, connector=fake_connector(session))
    expected = await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})
    replay = Hub(configs(), store)
    assert await replay.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"}) == expected
    assert store.catalog("jira")["origin"] == "recorded"
    assert "ORD-118" not in json.dumps(hub.audit)
    with pytest.raises(FixtureMissing):
        await replay.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-999"})
    with pytest.raises(ConfigurationError):
        Hub(configs(), store, demo_mode=True, record_mode=True)
    assert key_for("github", "list_pull_requests", {"a": 1, "b": 2}) == key_for(
        "github", "list_pull_requests", {"b": 2, "a": 1}
    )


async def test_st1_fix_1_no_recording_remote_errors_or_secrets(tmp_path):
    session = FakeSession([tool()], result=CallToolResult(content=[], isError=True))
    hub = Hub(
        configs(),
        FixtureStore(tmp_path),
        demo_mode=False,
        record_mode=True,
        connector=fake_connector(session),
    )
    with pytest.raises(RemoteToolError):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})
    assert not list(tmp_path.glob("**/*.json"))
    store = FixtureStore(tmp_path, secrets=("secret-only-value",))
    with pytest.raises(SecretInFixture):
        store.save("jira", "jira_get_issue", {}, {"content": [{"text": "secret-only-value"}]})
    with pytest.raises(SecretInFixture):
        store.save("jira", "jira_get_issue", {}, {"content": [{"text": '{"access_token":"hidden"}'}]})
    with pytest.raises(FixtureInvalid):
        store.save("jira", "jira_get_issue", {}, {"isError": True})
    assert not list(tmp_path.glob("**/*.json"))


@pytest.mark.parametrize("mutation", ["wrong_identity", "invalid_json", "no_origin", "error_result"])
def test_st1_fix_1_fixture_integrity(tmp_path, mutation):
    store = FixtureStore(tmp_path)
    store.save("jira", "jira_get_issue", {}, {"content": []})
    file = next(tmp_path.glob("**/*.json"))
    value = json.loads(file.read_text(encoding="utf-8"))
    if mutation == "wrong_identity":
        value["tool"] = "wrong"
    elif mutation == "no_origin":
        value.pop("origin")
    elif mutation == "error_result":
        value["result"]["isError"] = True
    file.write_text("not json" if mutation == "invalid_json" else json.dumps(value), encoding="utf-8")
    with pytest.raises(FixtureInvalid):
        store.load("jira", "jira_get_issue", {})


def test_st1_fix_1_paths_catalogs_and_origins(tmp_path):
    with pytest.raises(FixtureInvalid):
        FixtureStore(tmp_path, step="../bad")
    store = FixtureStore(tmp_path)
    with pytest.raises(FixtureInvalid):
        store.catalog("../bad")
    with pytest.raises(FixtureInvalid):
        store.save_catalog("jira", [], origin="pretend-official")
    with pytest.raises(FixtureInvalid):
        store.save("jira", "jira_get_issue", {}, {}, origin="unknown")
    store.save_catalog("jira", [])
    path = tmp_path / "step_1/jira/catalog.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["server"] = "wrong"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(FixtureInvalid):
        store.catalog("jira")


async def test_st1_mcp_1_pagination_and_failure_are_independent(tmp_path):
    class Paginated(FakeSession):
        async def list_tools(self, cursor=None):
            return SimpleNamespace(
                tools=[tool("jira_get_issue" if cursor is None else "jira_search")],
                nextCursor="page2" if cursor is None else None,
            )

    hub = Hub(
        configs(),
        FixtureStore(tmp_path),
        demo_mode=False,
        record_mode=True,
        connector=fake_connector(Paginated()),
    )
    assert len((await hub.discover("jira"))["tools"]) == 2
    with pytest.raises(ConfigurationError):
        await hub.discover("unknown")

    class Loop(FakeSession):
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[], nextCursor="same")

    hub.connector = fake_connector(Loop())
    with pytest.raises(SourceUnavailable):
        await hub.discover("jira")
    hub.connector = fake_connector(FakeSession([tool(), tool()]))
    with pytest.raises(SourceUnavailable):
        await hub.discover("jira")

    @asynccontextmanager
    async def failed(config, **kwargs):
        raise RuntimeError("credential-not-for-journal")
        yield

    hub.connector = failed
    reports = await hub.probe()
    assert len(reports) == 4 and all(r["status"] == "source_unavailable" for r in reports)
    with pytest.raises(SourceUnavailable):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})
    assert "credential-not-for-journal" not in json.dumps(hub.audit)


async def test_st1_mcp_1_missing_endpoint_is_configuration_error():
    with pytest.raises(ConfigurationError):
        async with sdk_session(ServerConfig(name="jira")):
            pytest.fail("Missing endpoint was accepted")


@pytest.fixture
async def http_mcp_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT), "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(400):
                if process.poll() is not None:
                    pytest.fail("Synthetic MCP server exited")
                try:
                    await client.get(f"http://127.0.0.1:{port}/mcp", timeout=0.2)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.025)
            else:
                pytest.fail("Synthetic MCP server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_st1_mcp_1_real_sdk_protocol_four_tools(tmp_path, http_mcp_server, transport):
    kwargs = {"transport": "stdio", "command": sys.executable, "args": [str(SERVER_SCRIPT)]}
    if transport == "http":
        kwargs = {"url": http_mcp_server}
    hub = Hub(configs(**kwargs), FixtureStore(tmp_path), demo_mode=False, record_mode=True)
    for server, sample in SCENARIO["calls"].items():
        catalog = await hub.discover(server)
        assert catalog["verified_live"] is True
        result = await hub.call("stage1_probe", server, sample["tool"], sample["arguments"])
        assert not result.get("isError")
        assert "synthetic" in result["content"][0]["text"]
    offline = Hub(configs(), FixtureStore(tmp_path))
    assert all(row["required_available"] for row in await offline.probe())
    assert all(row["outcome"] == "ok" for row in hub.audit)


async def test_st1_ro_1_sdk_remote_error(tmp_path, http_mcp_server):
    hub = Hub(configs(url=http_mcp_server), FixtureStore(tmp_path), demo_mode=False, record_mode=True)
    with pytest.raises(RemoteToolError):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ERROR"})
    assert hub.audit[-1]["outcome"] == "remote_tool_error"


async def test_st1_ro_1_cancellation_and_secret_redaction(tmp_path):
    session = FakeSession([tool()])
    hub = Hub(
        configs(token="sensitive-test-value"),
        FixtureStore(tmp_path),
        demo_mode=False,
        connector=fake_connector(session),
    )
    await hub.call(
        "stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"}, user_id="sensitive-test-value"
    )
    assert hub.audit[-1]["user_id"] == "redacted"

    @asynccontextmanager
    async def cancelled(config, **kwargs):
        raise asyncio.CancelledError
        yield

    hub.connector = cancelled
    with pytest.raises(asyncio.CancelledError):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})
    assert hub.audit[-1]["outcome"] == "cancelled"


async def test_st1_mcp_1_sdk_exception_groups_preserve_public_errors(tmp_path):
    from buddy.mcp_hub.errors import AuthenticationRequired
    from buddy.mcp_hub.hub import public_error

    public = InvalidArguments("public")
    assert public_error(ExceptionGroup("hidden", [public])) is public
    request = httpx.Request("POST", "https://example.invalid/mcp")
    error = httpx.HTTPStatusError("hidden", request=request, response=httpx.Response(401, request=request))
    assert isinstance(public_error(ExceptionGroup("hidden", [error])), AuthenticationRequired)

    @asynccontextmanager
    async def grouped(config, **kwargs):
        raise ExceptionGroup("hidden", [public])
        yield

    hub = Hub(configs(), FixtureStore(tmp_path), demo_mode=False, connector=grouped)
    with pytest.raises(InvalidArguments):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})
    assert hub.audit[-1]["outcome"] == "invalid_arguments"


async def test_st1_mcp_1_timeouts_and_page_limit(tmp_path):
    @asynccontextmanager
    async def slow(config, **kwargs):
        await asyncio.sleep(1)
        yield

    hub = Hub(configs(timeout=0.01), FixtureStore(tmp_path), demo_mode=False, connector=slow)
    with pytest.raises(SourceUnavailable):
        await hub.call("stage1_probe", "jira", "jira_get_issue", {"issue_key": "ORD-118"})

    class TooManyPages(FakeSession):
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[], nextCursor=str(int(cursor or "0") + 1))

    with pytest.raises(SourceUnavailable):
        await hub._tools(TooManyPages())
