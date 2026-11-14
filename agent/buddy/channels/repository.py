"""Private persistence API and isolated offline counterpart. No credentials in errors."""

import copy
import hashlib
import secrets
import time

import httpx


class ChannelError(Exception):
    def __init__(self, kind="unavailable"):
        self.kind = kind
        super().__init__(kind)


class BackendRepository:
    def __init__(self, url, key):
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"X-Buddy-Key": key},
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )

    async def call(self, operation, **data):
        try:
            response = await self.client.post("/internal/client/" + operation, json=data)
            if response.status_code != 200:
                raise ChannelError(
                    {400: "invalid", 404: "not_found", 409: "conflict"}.get(
                        response.status_code, "unavailable"
                    )
                )
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise ChannelError() from None

    async def redirect(self, token, *, head=False):
        from urllib.parse import urlsplit

        try:
            response = await (self.client.head if head else self.client.get)("/r/" + token)
            target = response.headers.get("Location", "")
            parsed = urlsplit(target)
            if (
                response.status_code != 302
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ChannelError("not_found")
            return target
        except httpx.HTTPError:
            raise ChannelError() from None

    async def close(self):
        await self.client.aclose()


class MemoryRepository:
    """Only for synthetic demo/tests; never a fallback for unavailable live storage."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.records, self.events, self.devices, self.sessions = {}, [], {}, {}
        self.seq = 0

    async def call(self, operation, **data):
        user, kind, ident = data.get("user_id", ""), data.get("kind", ""), data.get("id", "")
        now = self.clock()
        if operation == "device":
            secret = secrets.token_urlsafe(32)
            code = hashlib.sha256(secret.encode()).hexdigest()[:10].upper()
            self.devices[secret] = {"code": code, "until": now + 300, "user": ""}
            return {"device_secret": secret, "user_code": code, "interval": 5, "expires_in": 300}
        if operation == "approve":
            for record in self.devices.values():
                if record["code"] == data.get("code") and record["until"] > now and not record["user"]:
                    record["user"] = user
                    return {"ok": True}
            raise ChannelError("not_found")
        if operation == "token":
            record = self.devices.get(data.get("secret"))
            if not record or record["until"] <= now:
                raise ChannelError("not_found")
            if not record["user"]:
                return {"pending": True}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {
                "user_id": record["user"],
                "team_id": "demo-orders",
                "role": "intern",
                "display_name": "Демо",
                "until": now + 2592000,
            }
            del self.devices[data["secret"]]
            return {"token": token, "expires_in": 2592000}
        if operation == "session":
            value = self.sessions.get(data.get("secret"))
            if not value or value["until"] <= now:
                raise ChannelError("not_found")
            return copy.deepcopy(value)
        if operation == "telegram":
            return {"chat_id": None}
        if operation == "logout":
            self.sessions.pop(data.get("secret"), None)
            return {"ok": True}
        if operation == "interrupt":
            for (u, k, ident), value in self.records.items():
                if k == "request" and value["payload"].get("status") == "running":
                    value["payload"]["status"] = "interrupted"
                    self.seq += 1
                    self.events.append(
                        {
                            "user": u,
                            "seq": self.seq,
                            "kind": "request",
                            "payload": {
                                "id": ident,
                                "record": copy.deepcopy(value["payload"]),
                                "seq": value["seq"],
                            },
                        }
                    )
            return {"ok": True}
        key = (user, kind, ident)
        if operation in {"put", "create"}:
            if operation == "create" and key in self.records:
                return {"created": False, "payload": copy.deepcopy(self.records[key]["payload"])}
            self.seq += 1
            payload = copy.deepcopy(data["payload"])
            previous = self.records.get(key)
            row = {
                "id": ident,
                "seq": previous["seq"] if previous else self.seq,
                "payload": payload,
                "created_at": previous["created_at"] if previous else now,
            }
            self.records[key] = row
            self.events.append(
                {
                    "user": user,
                    "seq": self.seq,
                    "kind": kind,
                    "payload": {"id": ident, "record": copy.deepcopy(payload), "seq": row["seq"]},
                }
            )
            return {"created": True, "payload": payload, "seq": self.seq}
        if operation == "get":
            if key not in self.records:
                raise ChannelError("not_found")
            return copy.deepcopy(self.records[key]["payload"])
        if operation == "list":
            before = data.get("before", 0)
            rows = [
                r
                for (u, k, _), r in self.records.items()
                if u == user and k == kind and (not before or r["seq"] < before)
            ]
            return {"items": copy.deepcopy(sorted(rows, key=lambda r: r["seq"], reverse=True)[:100])}
        if operation == "events":
            return {
                "items": copy.deepcopy(
                    [
                        {k: v for k, v in r.items() if k != "user"}
                        for r in self.events
                        if r["user"] == user
                        and r["kind"] in {"message", "request"}
                        and r["seq"] > data.get("after", 0)
                    ][:100]
                )
            }
        raise ChannelError("invalid")
