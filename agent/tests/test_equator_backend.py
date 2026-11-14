"""FR-H4-12/15/16: REST shapes and authenticated H4 data routing."""

import json

import httpx
import pytest

from buddy.data.backend import BackendData
from buddy.data.base import DataError


async def test_fr_h4_12_rest_history_generation_and_identity():
    seen = []

    def respond(request):
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers["X-Buddy-User"],
                json.loads(request.content or b"null"),
            )
        )
        if request.url.path.endswith("mentor-chat"):
            return httpx.Response(200, json={"chat_id": 123})
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "h4-one"}]})
        if request.url.path.endswith("invalidate") or request.method == "DELETE":
            return httpx.Response(200, json={"generation": 2})
        return httpx.Response(200, json={"id": "h4-one"})

    client = BackendData("http://backend", "synthetic-key", transport=httpx.MockTransport(respond))
    try:
        assert await client.equator_history("intern") == [{"id": "h4-one"}]
        assert await client.equator_invalidate("intern") == {"generation": 2}
        await client.equator_save("intern", {"id": "h4-one", "generation": 2, "passed": True})
        await client.equator_claim("intern", "h4-one", "file", retry_unknown=True)
        await client.equator_delivery("intern", "h4-one", "file", "sent")
        assert await client.mentor_chat("intern") == 123
        assert await client.equator_clear("intern") == {"generation": 2}
    finally:
        await client.close()
    assert all(row[2] == "intern" for row in seen)
    assert seen[2][0:2] == ("PUT", "/api/v1/equator/checks/h4-one")
    assert seen[3][3] == {"part": "file", "retry_unknown": True}
    assert seen[4][3] == {"part": "file", "state": "sent"}


async def test_fr_h4_16_rest_claim_conflict_propagates():
    client = BackendData(
        "http://backend",
        "synthetic-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(409, json={"error": "conflict"})),
    )
    try:
        with pytest.raises(DataError, match="conflict"):
            await client.equator_claim("intern", "h4-one", "file")
    finally:
        await client.close()
