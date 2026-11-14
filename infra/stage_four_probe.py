"""Check Stage 4 with the real BackendData, PostgreSQL and Grafana; synthetic data only."""

import argparse
import asyncio
import base64
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


async def agent_probe():
    import httpx

    from buddy.data.backend import BackendData
    from buddy.demo.controller import DemoController
    from buddy.mcp_hub.errors import ToolDenied

    base = os.environ["BACKEND_URL"]
    key = os.environ["BUDDY_SERVICE_KEY"]
    user = "demo-daniil"
    brief_id = "probe-" + uuid.uuid4().hex
    data = BackendData(base, key)
    try:
        assert (await data.profile(user))["team_id"] == "demo-orders"  # Real MCP SDK and audit.
        items = [
            {
                "id": "probe-change-" + uuid.uuid4().hex,
                "source": "jira",
                "type": "mention",
                "title": "Synthetic probe",
                "url": "https://example.org/synthetic#comment",
                "priority_class": "people",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
        saved = await data.save_brief(user, brief_id, items)
        token = saved["redirect_tokens"][items[0]["id"]]
        headers = {"X-Buddy-Key": key, "X-Buddy-User": user}
        async with httpx.AsyncClient(base_url=base, headers=headers, trust_env=False) as client:
            assert (await client.get("/r/" + token)).status_code == 404
            baseline = await data.calendar_snapshot(user)
            await data.brief_status(user, brief_id, "failed")
            assert await data.calendar_snapshot(user) == baseline
            calendar = {"events": {"synthetic-event": {"start": "12:00", "probe": brief_id}}}
            await data.brief_status(user, brief_id, "sent", calendar)
            assert (await client.head("/r/" + token)).status_code == 302
            assert (await data.brief(user, brief_id))["read_at"] is None
            for _ in range(2):
                response = await client.get("/r/" + token + "?url=https://invalid.example")
                assert response.status_code == 302
                assert response.headers["location"] == items[0]["url"]
                assert response.headers["referrer-policy"] == "no-referrer"
            read_at = (await data.brief(user, brief_id))["read_at"]
            await data.brief_status(user, brief_id, "sent", {"must_not_replace": True})
            assert (await data.brief(user, brief_id))["read_at"] == read_at
            assert (
                await client.get("/api/v1/briefs/" + brief_id, headers={"X-Buddy-User": "demo-kirill"})
            ).status_code == 404
        previous_settings = await data.brief_settings(user)
        try:
            await data.set_brief_settings(user, time="08:15", quiet=True)
            await data.close()
            data = BackendData(base, key)  # No process-local state survives this replacement.
            assert await data.calendar_snapshot(user) == calendar
            assert items[0]["id"] in await data.brief_seen(user)
            assert await data.last_brief_sent(user)
            assert (await data.brief_settings(user))["time"] == "08:15"
            stats = await data.brief_stats("demo-anna")
            own = next(row for row in stats if row["user_id"] == user)
            assert own["clicked"] >= 1
            assert (await data.brief_stats("demo-petr")) == []
        finally:
            await data.set_brief_settings(
                user, time=previous_settings["time"] or "", quiet=previous_settings["quiet"]
            )
        qid = "probe-h3-" + uuid.uuid4().hex
        await data.record_bot_answer(
            user,
            qid,
            "Synthetic question?",
            "Synthetic answer",
            ["https://example.org/synthetic-source"],
            "h3",
        )
        await data.rate_bot_answer(user, qid, False)
        await data.rate_bot_answer(user, qid, True)
        assert (await data.question(user, qid))["capability"] == "h3"
        controller = DemoController(True)
        controller.command(1)
        hub = controller.hub(1)
        hub.audit_sink = data.record_mcp_call
        await hub.call("team_q5", "jira", "jira_get_issue", {"issue_key": "ORD-118"}, user_id=user)
        try:
            await hub.call("team_q5", "jira", "jira_create_issue", {}, user_id=user)
        except ToolDenied:
            pass
        else:
            raise RuntimeError("Write tool was not denied")
        print(
            "Stage 4: real client, calendar, opaque redirects, restart, isolation, H3 and audit OK"
        )
    finally:
        await data.close()


def grafana_probe(compose):
    output = subprocess.run(
        [*compose, "config", "--format", "json"], capture_output=True, text=True, check=True
    )
    config = json.loads(output.stdout)["services"]["grafana"]
    password = config["environment"]["GRAFANA_VIEWER_PASSWORD"]
    port = next(item["published"] for item in config["ports"] if item["target"] == 3000)
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}))
    authorization = "Basic " + base64.b64encode(("viewer:" + password).encode()).decode()

    def api(method, path, body=None):
        request = Request(
            base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": authorization, "Content-Type": "application/json"},
        )
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    user = api("GET", "/api/user")
    assert user["login"] == "viewer" and not user["isGrafanaAdmin"]
    assert api("GET", "/api/dashboards/uid/ai-buddy")["dashboard"]["panels"]

    def query(sql):
        return api(
            "POST",
            "/api/ds/query",
            {
                "queries": [
                    {
                        "refId": "A",
                        "format": "table",
                        "datasource": {"uid": "buddy-postgres", "type": "grafana-postgresql-datasource"},
                        "rawSql": sql,
                    }
                ],
                "from": "now-1d",
                "to": "now",
            },
        )["results"]["A"]

    for view in ["metrics_briefs", "metrics_helpful", "metrics_access", "metrics_mcp", "metrics_agent"]:
        assert "error" not in query("SELECT * FROM " + view)
    try:
        result = query("SELECT text FROM questions")
    except HTTPError as error:
        assert error.code == 400
    else:
        assert "error" in result
    try:
        api("POST", "/api/dashboards/db", {"dashboard": {"title": "Forbidden probe"}})
    except HTTPError as error:
        assert error.code == 403
    else:
        raise RuntimeError("Viewer was allowed to create a dashboard")
    print("Grafana: Viewer can read all aggregate panels; raw questions and dashboard writes denied")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        asyncio.run(agent_probe())
    else:
        compose = shlex.split(args.compose)
        subprocess.run(
            [*compose, "exec", "-T", "agent", "python", "-", "--inside"],
            input=Path(__file__).read_text(),
            text=True,
            check=True,
        )
        grafana_probe(compose)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError):
        print("Stage 4 check failed; verify services, make seed and make grafana-setup", file=sys.stderr)
        sys.exit(1)
