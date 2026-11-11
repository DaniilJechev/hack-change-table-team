"""Our data sources: offline seed bundle and backend client (buddy-mcp reads, REST writes)."""

import json

import httpx
import pytest

from buddy.data import BackendData, DataError, SeedData, load_bundle
from buddy.data.backend import check_url, tool_payload


async def test_nfr_priv_1_seed_profile_card_and_catalog_follow_roles_and_teams():
    data = SeedData()
    profile = await data.profile("demo-daniil")
    assert profile["mentor_id"] == "demo-anna" and profile["teamlead_id"] == "demo-oleg"
    assert profile["logins"]["github"] == "demo-daniil"
    card = await data.team_card("demo-daniil")
    assert card["document"]["team_id"] == "demo-orders" and card["source_path"].startswith("https://")
    with pytest.raises(DataError) as error:
        await data.team_card("demo-kirill")
    assert error.value.kind == "not_found"
    assert len(await data.access_catalog("demo-daniil")) == 6
    assert len(await data.access_catalog("demo-anna")) == 5  # forbidden object is described for interns only
    assert await data.access_catalog("demo-kirill") == []
    with pytest.raises(DataError) as error:
        await data.profile("nobody")
    assert error.value.kind == "unauthorized"
    assert data.target("demo-orders-miro")["board_id"] == "demo-board-orders"
    assert DataError("weird").kind == "unavailable"
    await data.close()


async def test_st2_rest_1_seed_marks_snapshots_and_interns():
    data = SeedData()
    first = await data.mark_requested("demo-daniil", "demo-orders-miro")
    assert await data.mark_requested("demo-daniil", "demo-orders-miro") == first
    assert await data.access_marks("demo-daniil") == {"demo-orders-miro": first}
    for object_id in ["demo-orders-production", "missing"]:
        with pytest.raises(DataError):
            await data.mark_requested("demo-daniil", object_id)
    with pytest.raises(DataError) as error:
        await data.interns("demo-daniil")
    assert error.value.kind == "forbidden"
    await data.save_snapshot("demo-daniil", "s1", [{"object_id": "demo-orders-miro", "status": "requested"}])
    interns = await data.interns("demo-anna")
    assert [item["user_id"] for item in interns] == ["demo-daniil"]
    assert interns[0]["snapshot"]["id"] == "s1"
    assert [item["user_id"] for item in await data.interns("demo-oleg")] == ["demo-daniil"]


async def test_st2_rest_1_seed_questions_answers_and_faq_rights():
    data = SeedData()
    question = await data.create_question("demo-daniil", "q1", "Почему Kafka?", "demo-petr")
    assert await data.create_question("demo-daniil", "q1", "Почему Kafka?", "demo-petr") == question
    for args, kind in [
        (("demo-daniil", "q1", "Другой текст", "demo-petr"), "conflict"),
        (("demo-daniil", "q2", "  ", "demo-petr"), "invalid"),
        (("demo-daniil", "q3", "Вопрос", "demo-kirill"), "invalid"),
    ]:
        with pytest.raises(DataError) as error:
            await data.create_question(*args)
        assert error.value.kind == kind
    with pytest.raises(DataError):
        await data.question("demo-oleg", "q1")  # neither author nor addressee
    with pytest.raises(DataError) as error:
        await data.answer_question("demo-daniil", "q1", "сам себе")
    assert error.value.kind == "forbidden"
    with pytest.raises(DataError):
        await data.rate_question("demo-daniil", "q1", True)  # no answer yet
    with pytest.raises(DataError):
        await data.answer_question("demo-petr", "q1", " ")
    answered = await data.answer_question("demo-petr", "q1", "Нужна шина событий")
    assert answered["answer"] == "Нужна шина событий"
    assert (await data.answer_question("demo-petr", "q1", "Нужна шина событий"))["answer"]
    with pytest.raises(DataError):
        await data.answer_question("demo-petr", "q1", "другой ответ")
    await data.rate_question("demo-daniil", "q1", True)
    assert (await data.question("demo-daniil", "q1"))["helpful"] is True
    with pytest.raises(DataError):
        await data.publish_faq("demo-daniil", "f1", "q1")  # only the answer author
    entry = await data.publish_faq("demo-petr", "f1", "q1")
    assert entry["author_id"] == "demo-petr"
    assert (await data.faq_search("demo-anna", "kafka"))[0]["id"] == "f1"
    assert await data.faq_search("demo-kirill", "kafka") == []
    assert await data.faq_search("demo-anna", "  ") == []


def test_st3_data_1_bundle_matches_seed_generator():
    bundle = load_bundle()
    assert set(bundle) == {"users", "card", "catalog", "targets", "calendar_baseline"}
    assert bundle["card"]["stage"]["epic_key"] == "ORD-100"
    assert set(bundle["targets"]) == {item["id"] for item in bundle["catalog"]["objects"]}


def test_st3_data_1_tool_payload_and_url_validation():
    assert tool_payload({"structuredContent": {"a": 1}}) == {"a": 1}
    assert tool_payload(
        {"content": [{"type": "text", "text": "x"}, {"type": "text", "text": '{"b":2}'}]}
    ) == {"b": 2}
    for text, kind in [
        ("Доступ запрещён", "forbidden"),
        ("Данные не найдены", "not_found"),
        ("??", "unavailable"),
    ]:
        with pytest.raises(DataError) as error:
            tool_payload({"isError": True, "content": [{"type": "text", "text": text}]})
        assert error.value.kind == kind
    with pytest.raises(DataError):
        tool_payload({"content": [{"type": "text", "text": "[1]"}]})

    class Result:
        def model_dump(self, **_):
            return {"structuredContent": {"c": 3}}

    assert tool_payload(Result()) == {"c": 3}
    for url in ["ftp://x", "http://user:pass@host", "http://host?q=1", "nohost"]:
        with pytest.raises(ValueError):
            check_url(url)
    assert check_url("http://backend:8080/") == "http://backend:8080"


def backend(handler, mcp=None):
    return BackendData("http://backend:8080", "service-key", transport=httpx.MockTransport(handler), mcp=mcp)


async def test_st2_mcp_1_backend_reads_through_buddy_mcp_with_user_header():
    calls = []

    async def mcp(user_id, tool, arguments):
        calls.append((user_id, tool, arguments))
        return {
            "user_profile_get": {"user_id": user_id},
            "team_card_get": {"document": {}},
            "team_faq_search": {"items": [{"id": "f1"}]},
            "access_catalog_get": {"items": [{"id": "o1"}]},
            "access_marks_get": {"items": [{"object_id": "o1", "requested_at": "2026-11-10T00:00:00+00:00"}]},
            "interns_list": {"items": []},
        }[tool]

    data = backend(lambda request: httpx.Response(500), mcp)
    assert (await data.profile("u1"))["user_id"] == "u1"
    assert (await data.team_card("u1"))["document"] == {}
    assert await data.faq_search("u1", "Kafka") == [{"id": "f1"}]
    assert await data.access_catalog("u1") == [{"id": "o1"}]
    assert await data.access_marks("u1") == {"o1": "2026-11-10T00:00:00+00:00"}
    assert await data.interns("u1") == []
    assert ("u1", "team_faq_search", {"text": "Kafka"}) in calls
    assert data.target("o1") is None
    await data.close()


async def test_st2_mcp_1_backend_read_failures_are_safe():
    async def broken(*_):
        raise RuntimeError("secret backend detail")

    async def denied(*_):
        raise DataError("forbidden")

    with pytest.raises(DataError) as error:
        await backend(lambda r: httpx.Response(200), broken).profile("u1")
    assert error.value.kind == "unavailable" and "secret" not in str(error.value)
    with pytest.raises(DataError) as error:
        await backend(lambda r: httpx.Response(200), denied).interns("u1")
    assert error.value.kind == "forbidden"


async def test_st2_rest_1_backend_writes_only_our_data_with_user_header():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers["X-Buddy-User"]))
        assert request.headers["X-Buddy-Key"] == "service-key"
        body = json.loads(request.content) if request.content else None
        if request.url.path.startswith("/api/v1/access-marks/"):
            return httpx.Response(200, json={"object_id": "o 1", "requested_at": "2026-11-10T00:00:00+00:00"})
        if request.url.path == "/api/v1/access-snapshots":
            return httpx.Response(201, json={"id": body["id"], "objects": body["objects"]})
        if request.method == "PATCH":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "q1", "user_id": "u1", "text": "t", "answer": "a"})

    data = backend(handler)
    assert await data.mark_requested("u1", "o 1") == "2026-11-10T00:00:00+00:00"
    await data.save_snapshot("u1", "s1", [{"object_id": "o1", "status": "granted"}])
    assert (await data.create_question("u1", "q1", "t", "u2"))["id"] == "q1"
    assert (await data.question("u1", "q1"))["id"] == "q1"
    assert (await data.answer_question("u2", "q1", "a"))["answer"] == "a"
    await data.rate_question("u1", "q1", True)
    assert (await data.publish_faq("u2", "f1", "q1"))["id"] == "q1"
    assert ("PUT", "/api/v1/access-marks/o%201", "u1") in seen or (
        "PUT",
        "/api/v1/access-marks/o 1",
        "u1",
    ) in seen
    assert ("PATCH", "/api/v1/questions/q1", "u1") in seen
    assert ("POST", "/api/v1/faq", "u2") in seen


@pytest.mark.parametrize(
    "response, kind",
    [
        (httpx.Response(400), "invalid"),
        (httpx.Response(401), "unauthorized"),
        (httpx.Response(403), "forbidden"),
        (httpx.Response(404), "not_found"),
        (httpx.Response(409), "conflict"),
        (httpx.Response(502), "unavailable"),
        (httpx.Response(200, text="not json"), "unavailable"),
        (httpx.Response(200, json=[1, 2]), "unavailable"),
    ],
)
async def test_st2_rest_1_backend_status_mapping(response, kind):
    with pytest.raises(DataError) as error:
        await backend(lambda request: response).question("u1", "q1")
    assert error.value.kind == kind


async def test_st2_rest_1_backend_network_error_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(DataError) as error:
        await backend(handler).question("u1", "q1")
    assert error.value.kind == "unavailable"
    empty = backend(lambda request: httpx.Response(200, content=b""))
    assert await empty.question("u1", "q1") == {}
