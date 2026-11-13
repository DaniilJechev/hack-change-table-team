"""Read-only entrypoint for Office-PowerPoint-MCP-Server 2.0.7 (AI-buddy H4).

Upstream registers ~30 tools, most of them write or save files. We keep exactly the
two read tools H4 needs and fail on start if the pinned registry layout changes.
open_presentation takes a .pptx file name inside PPTX_ROOT (the shared upload volume) only.
"""

import functools
import os
from pathlib import Path

import ppt_mcp_server as upstream

ALLOWED = ("open_presentation", "extract_presentation_text")
ROOT = Path(os.environ.get("PPTX_ROOT", "/presentations")).resolve()
MAX_OPEN = 20

registry = getattr(getattr(upstream.app, "_tool_manager", None), "_tools", None)
if not isinstance(registry, dict) or set(ALLOWED) - set(registry) or len(registry) < 10:
    raise SystemExit("Review upstream tool registry before enabling the PowerPoint MCP")
for name in list(registry):
    if name not in ALLOWED:
        del registry[name]

opener = registry["open_presentation"]
original = opener.fn


@functools.wraps(original)
def open_inside_root(file_path: str, id: str | None = None):
    # file_path is a file name inside PPTX_ROOT; absolute paths and ../ resolve outside and are refused.
    path = (ROOT / file_path).resolve()
    if path.parent != ROOT or path.suffix.lower() != ".pptx":
        return {"error": "File is outside the presentation folder"}
    # Upstream keeps every opened deck in memory; drop the oldest ones.
    while len(upstream.presentations) >= MAX_OPEN:
        upstream.presentations.pop(next(iter(upstream.presentations)))
    result = original(str(path), id)
    if isinstance(result, dict) and "message" in result:
        # Do not reveal the server's filesystem layout to the client.
        result["message"] = "Opened presentation " + path.name
    return result


opener.fn = open_inside_root

if __name__ == "__main__":
    from mcp.server.transport_security import TransportSecuritySettings

    upstream.app.settings.host = os.environ.get("HOST", "0.0.0.0")
    upstream.app.settings.port = int(os.environ.get("PORT", "8000"))
    # Upstream builds FastMCP for 127.0.0.1, so DNS-rebinding protection admits only localhost.
    # Keep the protection and add the Compose service name the agent uses.
    hosts = os.environ.get("ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,powerpoint-mcp:*").split(",")
    upstream.app.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=[host.strip() for host in hosts if host.strip()]
    )
    upstream.app.run(transport="streamable-http")
