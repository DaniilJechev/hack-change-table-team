"""Verify running stage-0 endpoints without contacting corporate services."""

import json
import os
from urllib.request import urlopen

for name, port, path in [
    ("backend", os.getenv("BACKEND_PORT", "8080"), "/health"),
    ("agent", os.getenv("AGENT_PORT", "8081"), "/health"),
    ("grafana", os.getenv("GRAFANA_PORT", "3000"), "/api/health"),
]:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
        body = json.load(response)
    if name == "grafana":
        assert body.get("database") == "ok", body
    else:
        assert body.get("status") == "ok" and body.get("service") == name, body
    print(f"{name}: OK")
