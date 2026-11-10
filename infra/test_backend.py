"""Run Go integration tests against an isolated disposable PostgreSQL container."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = os.environ.copy()
    container = None
    try:
        if not environment.get("TEST_DATABASE_URL"):
            container = "buddy-test-" + uuid.uuid4().hex[:12]
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--publish",
                    "127.0.0.1::5432",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "pgvector/pgvector:pg16",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            for _ in range(60):
                result = subprocess.run(
                    ["docker", "exec", container, "pg_isready", "-U", "postgres"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Test PostgreSQL did not become ready")
            address = subprocess.check_output(
                ["docker", "port", container, "5432"], text=True, timeout=10
            ).strip()
            environment["TEST_DATABASE_URL"] = f"postgres://postgres@{address}/postgres?sslmode=disable"
        command = [
            os.environ.get("GO", "go"),
            "test",
            "./...",
            "-count=1",
            "-coverprofile=coverage.out",
            "-covermode=atomic",
        ]
        if environment.get("GO_RACE") == "1":
            command.append("-race")
        subprocess.run(command, cwd=ROOT / "backend", env=environment, check=True)
    finally:
        if container:
            subprocess.run(["docker", "stop", container], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Backend integration check failed: {error}", file=sys.stderr)
        sys.exit(1)
