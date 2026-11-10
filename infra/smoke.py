"""Acceptance checks for the running stage-zero Compose stack."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen

COMPOSE_COMMAND = None


def compose(*arguments: str) -> str:
    command = shlex.split(COMPOSE_COMMAND or os.environ.get("COMPOSE", "docker compose"))
    return subprocess.run(
        [*command, *arguments], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="require DEMO_MODE=true")
    parser.add_argument("--compose", default=None)
    arguments = parser.parse_args()
    global COMPOSE_COMMAND
    COMPOSE_COMMAND = arguments.compose

    output = compose("ps", "--all", "--format", "json")
    rows = (
        json.loads(output)
        if output.startswith("[")
        else [json.loads(line) for line in output.splitlines() if line.strip()]
    )
    expected = {"backend", "agent", "postgres", "redis", "grafana"}
    names = {row["Service"] for row in rows}
    check(
        expected <= names <= expected | {"mcp-atlassian", "google-calendar-mcp"},
        "Expected five core containers and only configured optional MCP servers",
    )
    for row in rows:
        if row["Service"] not in expected:
            continue  # Optional MCP servers have a separate make mcp-list check.
        check(
            row["State"] == "running" and row.get("Health") == "healthy",
            f"Service {row['Service']} is not running and healthy",
        )
    print("NFR-BOOT-1: all five containers are running and healthy")

    address = compose("port", "backend", "8080")
    with urlopen(f"http://{address}/health", timeout=5) as response:
        check(
            response.headers.get_content_type() == "application/json",
            "Backend health response must be JSON",
        )
        body = json.load(response)
        check(
            body.get("status") == "ok" and body.get("service") == "backend" and body.get("stage") == "0",
            "Unexpected backend health response",
        )

    compose(
        "exec",
        "-T",
        "agent",
        "python",
        "-c",
        """
import json
from urllib.request import urlopen
for service, port in [('backend', 8080), ('agent', 8081)]:
    with urlopen(f'http://{service}:{port}/health', timeout=5) as response:
        assert response.headers.get_content_type() == 'application/json'
        body = json.load(response)
        assert body['status'] == 'ok' and body['service'] == service and body['stage'] == '0'
with urlopen('http://grafana:3000/api/health', timeout=5) as response:
    assert json.load(response)['database'] == 'ok'
""",
    )
    print("NFR-HEALTH-1: backend, agent and Grafana HTTP checks passed")

    check(
        compose("exec", "-T", "redis", "redis-cli", "ping") == "PONG",
        "Redis did not return PONG",
    )
    vector = compose(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 '
        '''-c "SELECT '[1,2,3]'::vector;"''',
    )
    check(vector == "[1,2,3]", "PostgreSQL vector extension is not working")
    print("NFR-BOOT-1: Redis and PostgreSQL vector checks passed")

    if arguments.demo:
        compose(
            "exec",
            "-T",
            "agent",
            "python",
            "-c",
            "import os; assert os.environ['DEMO_MODE'] == 'true'",
        )
        print("NFR-ENV-1: DEMO_MODE=true is set in agent")


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        ValueError,
        KeyError,
        OSError,
        URLError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Smoke check failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        sys.exit(1)
