"""Public desktop API. Only session-derived profiles cross the ChatService boundary."""

import asyncio
import base64
import json
import secrets
import time
from collections import defaultdict, deque
from pathlib import PurePath

from aiohttp import web

from buddy.channels.repository import ChannelError
from buddy.channels.service import owner


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except ChannelError as error:
        status = {
            "invalid": 400,
            "not_found": 404,
            "unauthorized": 401,
            "forbidden": 403,
            "conflict": 409,
            "limited": 429,
        }.get(error.kind, 503)
        return web.json_response(
            {"error": {"code": error.kind, "message": "Проверьте подключение, вход и параметры запроса."}},
            status=status,
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return web.json_response(
            {"error": {"code": "invalid", "message": "Некорректный запрос."}}, status=400
        )


class ClientAPI:
    def __init__(self, chat, *, demo=False):
        self.chat, self.repo, self.demo = chat, chat.repository, demo
        chat.api = self
        self.hits = defaultdict(deque)

    def limit(self, key, maximum):
        now = time.monotonic()
        if len(self.hits) > 5000:
            self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v and v[-1] > now - 60})
        queue = self.hits[key]
        while queue and queue[0] < now - 60:
            queue.popleft()
        if len(queue) >= maximum:
            raise ChannelError("limited")
        queue.append(now)

    async def body(self, request, allowed):
        data = await request.json()
        if not isinstance(data, dict) or set(data) - set(allowed):
            raise ChannelError("invalid")
        return data

    async def profile(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header[7:]) != 43:
            raise ChannelError("unauthorized")
        try:
            return await self.repo.call("session", secret=header[7:])
        except ChannelError as error:
            if error.kind == "not_found":
                raise ChannelError("unauthorized") from None
            raise

    async def redirect(self, request):
        import re

        token = request.match_info["token"]
        if self.demo or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
            raise ChannelError("not_found")
        target = (
            await self.repo.redirect(token, head=True)
            if request.method == "HEAD"
            else await self.repo.redirect(token)
        )
        return web.Response(
            status=302,
            headers={"Location": target, "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
        )

    async def info(self, request):
        return web.json_response(
            {
                "service": "avitocode",
                "api_version": 1,
                "demo": self.demo,
                "capabilities": ["H0", "H1", "H2", "H3", "H4"],
            }
        )

    async def device(self, request):
        if self.demo:
            raise ChannelError("not_found")
        self.limit((request.remote, "device"), 10)
        await self.body(request, [])
        return web.json_response(await self.repo.call("device"), headers={"Cache-Control": "no-store"})

    async def token(self, request):
        self.limit((request.remote, "token"), 60)
        data = await self.body(request, ["device_secret"])
        secret = data.get("device_secret", "")
        if not isinstance(secret, str) or len(secret) != 43:
            raise ChannelError("invalid")
        return web.json_response(
            await self.repo.call("token", secret=secret), headers={"Cache-Control": "no-store"}
        )

    async def demo_session(self, request):
        if not self.demo:
            raise ChannelError("not_found")
        self.limit((request.remote, "demo"), 10)
        await self.body(request, [])
        token = secrets.token_urlsafe(32)
        session = -secrets.randbelow(2**52) - 1
        self.chat.agent.demo.command(session)
        context = self.chat.agent.demo.context(session)
        self.repo.sessions[token] = {
            "user_id": context.user_id,
            "team_id": context.team_id,
            "role": context.role,
            "display_name": "Синтетическое демо",
            "owner": "demo:" + str(session),
            "demo": True,
            "session": session,
            "until": time.time() + 8 * 3600,
        }
        return web.json_response({"token": token, "expires_in": 8 * 3600})

    async def me(self, request):
        p = await self.profile(request)
        if p.get("demo"):
            ctx = self.chat.context(p)
            p.update(role=ctx.role, user_id=ctx.user_id)
        if not p.get("demo"):
            chat = await self.chat.telegram_chat(p)
            if chat:
                self.chat.agent.remember(self.chat.context(p), chat)
        return web.json_response(
            {k: v for k, v in p.items() if k in {"user_id", "team_id", "role", "display_name", "demo"}},
            headers={"Cache-Control": "no-store"},
        )

    async def logout(self, request):
        await self.profile(request)
        return web.json_response(await self.repo.call("logout", secret=request.headers["Authorization"][7:]))

    async def preferences(self, request):
        profile = await self.profile(request)
        if request.method == "PUT":
            data = await self.body(request, ["show_telegram_history", "mirror_desktop_to_telegram"])
            if set(data) != {"show_telegram_history", "mirror_desktop_to_telegram"} or any(
                type(value) is not bool for value in data.values()
            ):
                raise ChannelError("invalid")
            await self.repo.call(
                "put", user_id=owner(profile), kind="state", id="channel_preferences", payload=data
            )
        return web.json_response(await self.chat.preferences(profile), headers={"Cache-Control": "no-store"})

    async def messages(self, request):
        profile = await self.profile(request)
        value = await self.repo.call(
            "list", user_id=owner(profile), kind="message", before=int(request.query.get("before", 0))
        )
        return web.json_response(value, headers={"Cache-Control": "no-store"})

    async def requests(self, request):
        profile = await self.profile(request)
        return web.json_response(
            await self.repo.call("list", user_id=owner(profile), kind="request"),
            headers={"Cache-Control": "no-store"},
        )

    async def submit(self, request):
        profile = await self.profile(request)
        self.limit((owner(profile), "submit"), 30)
        data = await self.body(request, ["id", "text"])
        if not isinstance(data.get("text"), str) or not 1 <= len(data["text"].strip()) <= 16000:
            raise ChannelError("invalid")
        return web.json_response(
            await self.chat.submit(profile, data.get("id"), {"text": data["text"]}), status=202
        )

    async def action(self, request):
        profile = await self.profile(request)
        self.limit((owner(profile), "submit"), 30)
        data = await self.body(request, ["id", "message_id", "data"])
        if not all(isinstance(data.get(k), str) and len(data[k]) <= 160 for k in ("message_id", "data")):
            raise ChannelError("invalid")
        return web.json_response(
            await self.chat.submit(
                profile,
                data.get("id"),
                {"type": "action", "message_id": data["message_id"], "data": data["data"]},
            ),
            status=202,
        )

    async def upload(self, request):
        profile = await self.profile(request)
        self.limit((owner(profile), "submit"), 10)
        ident = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(ident) <= 100:
            raise ChannelError("invalid")
        if request.content_type != "multipart/form-data":
            raise ChannelError("invalid")
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            raise ChannelError("invalid")
        name = PurePath((part.filename or "").replace("\\", "/")).name
        if not name.lower().endswith((".pdf", ".pptx", ".yaml", ".yml", ".json")):
            raise ChannelError("invalid")
        data = bytearray()
        while chunk := await part.read_chunk():
            data.extend(chunk)
            if len(data) > 10 << 20:
                raise web.HTTPRequestEntityTooLarge(max_size=10 << 20, actual_size=len(data))
        if await reader.next() is not None:
            raise ChannelError("invalid")
        attachment = await self.chat.save_file(profile, name, bytes(data))
        return web.json_response(
            await self.chat.submit(profile, ident, {"attachment": attachment}), status=202
        )

    async def download(self, request):
        profile = await self.profile(request)
        file = await self.repo.call("get", user_id=owner(profile), kind="file", id=request.match_info["id"])
        from urllib.parse import quote

        return web.Response(
            body=base64.b64decode(file["data"]),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file["name"], safe=""),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def read(self, request):
        profile = await self.profile(request)
        data = await self.body(request, ["message_id"])
        message = await self.repo.call("get", user_id=owner(profile), kind="message", id=data["message_id"])
        await self.repo.call("put", user_id=owner(profile), kind="read", id="desktop", payload=data)
        brief = message["response"].get("brief_id")
        if brief:
            await self.chat.agent.brief_signal("read", brief, self.chat.context(profile))
        return web.json_response({"ok": True})

    async def events(self, request):
        profile = await self.profile(request)
        after = int(request.query.get("after", request.headers.get("Last-Event-ID", 0)))
        if after < 0:
            raise ChannelError("invalid")
        stream = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            }
        )
        await stream.prepare(request)
        preferences = None
        try:
            while True:
                await self.profile(request)  # revocation and expiration also stop an open stream
                current = await self.chat.preferences(profile)
                if current != preferences:
                    preferences = current
                    # Existing clients skip this event at their current cursor.
                    await stream.write(
                        (
                            "event: preferences\ndata: "
                            + json.dumps({"kind": "preferences", "seq": after, "payload": current})
                            + "\n\n"
                        ).encode()
                    )
                result = await self.repo.call("events", user_id=owner(profile), after=after)
                for event in result["items"]:
                    after = event["seq"]
                    await stream.write(
                        (
                            f"id: {after}\nevent: update\ndata: "
                            + json.dumps(event, ensure_ascii=False)
                            + "\n\n"
                        ).encode()
                    )
                if not result["items"]:
                    await stream.write(b": heartbeat\n\n")
                    await asyncio.sleep(2)
        except (ConnectionError, asyncio.CancelledError, ChannelError):
            pass
        return stream

    def mount(self, app):
        app.middlewares.append(errors)
        app.router.add_get("/r/{token}", self.redirect)
        routes = [
            ("GET", "/info", self.info),
            ("POST", "/auth/device", self.device),
            ("POST", "/auth/token", self.token),
            ("POST", "/auth/demo", self.demo_session),
            ("GET", "/me", self.me),
            ("DELETE", "/session", self.logout),
            ("GET", "/preferences", self.preferences),
            ("PUT", "/preferences", self.preferences),
            ("GET", "/chat/messages", self.messages),
            ("POST", "/chat/requests", self.submit),
            ("GET", "/chat/requests", self.requests),
            ("POST", "/chat/actions", self.action),
            ("POST", "/chat/files", self.upload),
            ("GET", "/files/{id}", self.download),
            ("PUT", "/chat/read", self.read),
            ("GET", "/events", self.events),
        ]
        for method, path, handler in routes:
            app.router.add_route(method, "/client/v1" + path, handler)

        async def startup(app):
            await self.repo.call("interrupt")
            if not self.demo:
                for profile in (await self.repo.call("audience"))["items"]:
                    chat = await self.chat.telegram_chat(profile)
                    if chat:
                        self.chat.agent.remember(self.chat.context(profile), chat)

        async def cleanup(app):
            await self.chat.close()

        app.on_startup.append(startup)
        app.on_cleanup.append(cleanup)
