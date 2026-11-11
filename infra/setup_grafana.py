"""Create/update the separate Grafana Viewer from Compose config without logging credentials."""

import argparse
import base64
import json
import shlex
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose")
    args = parser.parse_args()
    result = subprocess.run(
        [*shlex.split(args.compose), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)["services"]["grafana"]
    env = config["environment"]
    port = next(item["published"] for item in config["ports"] if item["target"] == 3000)
    base = f"http://127.0.0.1:{port}"
    credentials = env["GF_SECURITY_ADMIN_USER"] + ":" + env["GF_SECURITY_ADMIN_PASSWORD"]
    opener = build_opener(ProxyHandler({}))

    def api(method, path, body=None):
        request = Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
                "Content-Type": "application/json",
            },
        )
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    try:
        viewer = api("GET", "/api/users/lookup?loginOrEmail=viewer")
    except HTTPError as error:
        if error.code != 404:
            raise
        viewer = api(
            "POST",
            "/api/admin/users",
            {
                "name": "Просмотр метрик",
                "login": "viewer",
                "email": "viewer@example.invalid",
                "password": env["GRAFANA_VIEWER_PASSWORD"],
                "OrgId": 1,
            },
        )
    user_id = int(viewer["id"])
    api("PUT", f"/api/admin/users/{user_id}/permissions", {"isGrafanaAdmin": False})
    api("PATCH", f"/api/org/users/{user_id}", {"role": "Viewer"})
    api("PUT", f"/api/admin/users/{user_id}/password", {"password": env["GRAFANA_VIEWER_PASSWORD"]})
    print("Grafana: viewer account provisioned; dashboard uses aggregate-only PostgreSQL login")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
        print("Grafana setup failed: check service health and local Grafana credentials", file=sys.stderr)
        sys.exit(1)
