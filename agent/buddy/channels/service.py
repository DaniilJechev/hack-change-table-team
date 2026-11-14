"""Channel-independent application API. Both transports call this service."""

import asyncio
import base64
import hashlib
import json
import uuid
from contextlib import suppress

from buddy.channels.repository import ChannelError
from buddy.executors.context import Context
from buddy.executors.core import TEAM_MENU
from buddy.schemas import Action, ExecutorResponse


def owner(profile):
    return profile.get("owner", profile["user_id"])


class ChatService:
    def __init__(self, agent, repository, *, bot=None, identity=None):
        self.agent, self.repository, self.bot, self.identity = agent, repository, bot, identity
        self.locks, self.tasks = {}, set()

    def context(self, profile):
        if profile.get("demo"):
            return self.agent.demo.context(profile["session"])
        return Context(profile["user_id"], profile["team_id"], profile["role"])

    async def record(
        self, profile, response, *, role="assistant", channel="desktop", ident=None, attachments=None
    ):
        ident = ident or uuid.uuid4().hex
        payload = {
            "role": role,
            "channel": channel,
            "response": response.model_dump(),
            "attachments": attachments or [],
        }
        await self.repository.call(
            "create", user_id=owner(profile), kind="message", id=ident, payload=payload
        )
        return ident

    async def deliver_telegram(self, profile, response, message_id, *, full=False):
        if self.bot is None:
            return False
        chat = await self.telegram_chat(profile)
        if not chat:
            return False
        from buddy.bot.equator import report
        from buddy.bot.stage_two import keyboard, rich

        sent = None

        async def send_part(**kwargs):
            nonlocal sent
            sent = await self.bot.send_message(chat, **kwargs)
            return sent

        await (report if full else rich)(send_part, response, reply_markup=keyboard(response.actions))
        if sent is not None:
            await self.repository.call(
                "put",
                user_id=owner(profile),
                kind="state",
                id="tgmessage:" + str(sent.message_id),
                payload={"message_id": message_id},
            )
        return True

    async def background(self, profile, response, *, ident=None, attachments=None):
        message_id = await self.record(
            profile, response, channel="background", ident=ident, attachments=attachments
        )
        with suppress(Exception):
            await self.deliver_telegram(profile, response, message_id)
        return message_id

    async def telegram_chat(self, profile):
        if profile.get("demo"):
            return profile.get("session") if profile.get("session", 0) > 0 else None
        result = await self.repository.call("telegram", user_id=owner(profile))
        return result.get("chat_id") or self.agent.chats.get(profile["user_id"])

    async def state(self, profile):
        try:
            return await self.repository.call("get", user_id=owner(profile), kind="state", id="interaction")
        except ChannelError as error:
            if error.kind != "not_found":
                raise
            return {}

    async def preferences(self, profile):
        defaults = {"show_telegram_history": True, "mirror_desktop_to_telegram": True}
        try:
            saved = await self.repository.call(
                "get", user_id=owner(profile), kind="state", id="channel_preferences"
            )
        except ChannelError as error:
            if error.kind != "not_found":
                raise
            return defaults
        return {key: saved.get(key, value) for key, value in defaults.items()}

    async def set_state(self, profile, value):
        await self.repository.call(
            "put", user_id=owner(profile), kind="state", id="interaction", payload=value
        )

    async def submit(self, profile, ident, payload, channel="desktop"):
        if not isinstance(ident, str) or not 1 <= len(ident) <= 100:
            raise ChannelError("invalid")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        request = {"status": "running", "digest": digest, "channel": channel}
        saved = await self.repository.call(
            "create", user_id=owner(profile), kind="request", id=ident, payload=request
        )
        if not saved["created"]:
            if saved["payload"]["digest"] != digest:
                raise ChannelError("conflict")
            return {"id": ident, **saved["payload"]}
        task = asyncio.create_task(self.run(profile, ident, payload, request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"id": ident, **request}

    async def run(self, profile, ident, payload, request):
        async with self.locks.setdefault(owner(profile), asyncio.Lock()):
            try:
                if not profile.get("demo"):
                    profile = await self.repository.call("profile", user_id=owner(profile))
                context = self.context(profile)
                text = payload.get("text", "")
                attachment = payload.get("attachment")
                if payload.get("type") == "action":
                    message = await self.repository.call(
                        "get", user_id=owner(profile), kind="message", id=payload.get("message_id", "")
                    )
                    allowed = [a.get("data") for a in message["response"].get("actions", []) if a.get("data")]
                    if payload.get("data") not in allowed:
                        raise ChannelError("forbidden")
                    response = await self.action(profile, payload["data"])
                    if response.feedback_saved:
                        callback = payload["data"]
                        if callback.startswith("brief:"):
                            brief_id = callback.split(":", 2)[2]
                            consumed = {
                                f"brief:{kind}:{brief_id}" for kind in ("positive", "negative", "read")
                            }
                        elif callback.startswith("rate:"):
                            target = callback.rpartition(":")[0]
                            consumed = {target + ":0", target + ":1"}
                        else:
                            consumed = {callback}
                        message["response"]["actions"] = [
                            a for a in message["response"]["actions"] if a.get("data") not in consumed
                        ]
                        if (
                            request["channel"] == "telegram"
                            and self.bot
                            and payload.get("telegram_message_id")
                        ):
                            from buddy.bot.stage_two import keyboard

                            with suppress(Exception):
                                await self.bot.edit_message_reply_markup(
                                    chat_id=await self.telegram_chat(profile),
                                    message_id=payload["telegram_message_id"],
                                    reply_markup=keyboard(
                                        [Action.model_validate(a) for a in message["response"]["actions"]]
                                    ),
                                )
                        await self.repository.call(
                            "put",
                            user_id=owner(profile),
                            kind="message",
                            id=payload["message_id"],
                            payload=message,
                        )
                else:
                    await self.record(
                        profile,
                        ExecutorResponse(result=text or (attachment or {}).get("name", "")),
                        role="user",
                        channel=request["channel"],
                        ident=ident + ":user",
                        attachments=[attachment] if attachment else [],
                    )
                    if attachment:
                        response = await self.file(profile, attachment)
                    else:
                        response = await self.text(profile, text)
                message_id = await self.record(
                    profile, response, channel=request["channel"], ident=ident + ":answer"
                )
                request.update(status="completed", message_id=message_id)
                if request["channel"] == "telegram" and self.bot:
                    await self.deliver_telegram(profile, response, message_id, full="attachment" in payload)
                elif request["channel"] == "desktop" and not profile.get("demo") and self.bot:
                    # A failed mirror must not invalidate an already saved desktop answer
                    # or cause its agent operation to run again on a client retry.
                    with suppress(Exception):
                        if (await self.preferences(profile))["mirror_desktop_to_telegram"]:
                            await self.deliver_telegram(
                                profile, response, message_id, full="attachment" in payload
                            )
                if response.brief_id:
                    await self.agent.brief_delivered(response, context, True)
            except asyncio.CancelledError:
                request.update(
                    status="interrupted", error="Операция прервана. Проверьте результат перед повтором."
                )
                raise
            except Exception as error:
                kind = getattr(error, "kind", "unavailable")
                request.update(
                    status="failed",
                    error={
                        "forbidden": "Действие недоступно этому пользователю.",
                        "not_found": "Объект не найден или устарел.",
                    }.get(kind, "Не удалось выполнить запрос. Попробуйте позже."),
                )
            finally:
                with suppress(ChannelError):
                    await self.repository.call(
                        "put", user_id=owner(profile), kind="request", id=ident, payload=request
                    )

    async def text(self, profile, text):
        context = self.context(profile)
        state = await self.state(profile)
        if state.get("answer"):
            runtime = self.agent.runtime(context)
            notes = await self.agent.executors.team.deliver_answer(
                context.user_id, state["answer"], text, runtime
            )
            await self.set_state(profile, {})
            for recipient, response in notes:
                await self.notify(profile, recipient, response)
            return ExecutorResponse(result="Ответ сохранён и передан автору вопроса.")
        if state.get("card"):
            await self.set_state(profile, {})
            return await self.agent.upload_card(text.encode(), context)
        command, _, arguments = text.partition(" ")
        if command == "/demo" and profile.get("demo"):
            self.agent.clear_demo(profile["session"]) if arguments in {"reset", "mentor", "intern"} else None
            return ExecutorResponse(result=self.agent.demo.command(profile["session"], arguments))
        if command in {"/brief", "/access"}:
            return await self.agent.trigger("brief_now" if command == "/brief" else "access_status", context)
        if command == "/stats":
            return await self.agent.stats(context)
        if command == "/settings":
            return await self.agent.settings(context, arguments)
        if command == "/reset":
            await self.set_state(profile, {})
            return await self.agent.reset(context)
        if command == "/teamcard":
            if context.role not in {"mentor", "teamlead"}:
                return ExecutorResponse(result="Загрузка карточки доступна наставнику или тимлиду.")
            await self.set_state(profile, {"card": True})
            return ExecutorResponse(result="Пришлите JSON или YAML карточки файлом либо текстом.")
        if command == "/ask":
            return ExecutorResponse(
                result="Выберите вопрос о команде:",
                actions=[Action(label=label, data="case:" + case) for case, label in TEAM_MENU],
            )
        if command in {"/help", "/start"}:
            return ExecutorResponse(
                result="AvitoCode: /brief — бриф, /access — доступы, /ask — команда, "
                "/stats — статистика, /settings — настройки, /reset — забыть контекст. "
                "Пришлите PDF/PPTX для проверки к экватору."
            )
        decision, response = await self.agent.handle(text, context)
        if not response.actions:
            menus = {
                "clarify": [
                    ("Доступ к инструменту", "case:access_status"),
                    ("Кто это и можно ли ему писать", "case:team_q3"),
                ],
                "out_of_scope": [("Возможности", "case:help"), ("Спросить наставника", "mentor:contact")],
                "help": [(title, "case:" + case) for case, title in TEAM_MENU],
            }
            response.actions = [
                Action(label=label, data=data) for label, data in menus.get(decision.case, [])
            ]
        return response

    async def notify(self, sender, recipient, response, attachments=None):
        if sender.get("demo"):
            return await self.record(sender, response, channel="background", attachments=attachments)
        runtime = self.agent.runtime(self.context(sender))
        p = await runtime.data.profile(recipient)
        await self.background(p, response, attachments=attachments)

    async def action(self, profile, value):
        context = self.context(profile)
        kind, _, argument = value.partition(":")
        if kind == "mentor":
            return ExecutorResponse(result="Используйте «Спросить наставника» под ответом.")
        if kind == "case":
            return await self.agent.executors.execute(argument, {}, context, self.agent.runtime(context), "")
        if kind in {"request", "mark", "remind"}:
            method = {
                "request": self.agent.request_access,
                "mark": self.agent.mark,
                "remind": self.agent.reminder,
            }[kind]
            return await method(argument, context)
        if kind == "esc":
            response, notes = await self.agent.escalate(argument, context)
            for recipient, note in notes:
                await self.notify(profile, recipient, note)
            return response
        if kind == "ans":
            record = await self.agent.runtime(context).data.question(context.user_id, argument)
            if not context.demo and record["assigned_to"] != context.user_id:
                raise ChannelError("forbidden")
            await self.set_state(profile, {"answer": argument})
            return ExecutorResponse(result="Напишите ответ следующим сообщением.")
        if kind == "faq":
            return await self.agent.publish(argument, context)
        if kind == "nofaq":
            return ExecutorResponse(result="В FAQ не сохраняю.")
        if kind == "brief":
            action, _, brief_id = argument.partition(":")
            return await self.agent.brief_signal(action, brief_id, context)
        if kind == "rate":
            target, _, vote = argument.rpartition(":")
            response = await self.agent.rate(target, vote == "1", context)
            response.feedback_saved = response.result.startswith("Спасибо")
            return response
        if kind == "equator":
            return await self.equator_send(profile, argument)
        return ExecutorResponse(result="Действие устарело. Отправьте вопрос ещё раз.")

    async def save_file(self, profile, name, data):
        if not name or len(data) > 10 << 20:
            raise ChannelError("invalid")
        ident = hashlib.sha256(owner(profile).encode() + b"\0" + name.encode() + b"\0" + data).hexdigest()[
            :32
        ]
        # PostgreSQL byte payload is private, scoped, durable and backed up with chat.
        await self.repository.call(
            "create",
            user_id=owner(profile),
            kind="file",
            id=ident,
            payload={"name": name[:200], "data": base64.b64encode(data).decode()},
        )
        return {"id": ident, "name": name[:200]}

    async def file(self, profile, attachment):
        value = await self.repository.call("get", user_id=owner(profile), kind="file", id=attachment["id"])
        data = base64.b64decode(value["data"])
        context = self.context(profile)
        if value["name"].lower().endswith((".yaml", ".yml", ".json")):
            return await self.agent.upload_card(data, context)
        response, record = await self.agent.equator_check(
            value["name"], data, context, file_id="desktop:" + attachment["id"]
        )
        if record:
            await self.repository.call(
                "put",
                user_id=owner(profile),
                kind="state",
                id="equator:" + record.id,
                payload={"attachment": attachment},
            )
        return response

    async def equator_send(self, profile, check_id):
        from functools import partial

        from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
        from aiogram.types import BufferedInputFile

        from buddy.bot.equator import report

        retry_unknown = check_id.startswith("retry:")
        check_id = check_id.removeprefix("retry:")
        context = self.context(profile)
        runtime = self.agent.runtime(context)
        reply, recipient, note = await self.agent.equator_send(check_id, context)
        if not note or not recipient:
            return reply
        record = self.agent.equator.find(check_id, context)
        if record is None:
            return reply
        stored = await self.repository.call(
            "get", user_id=owner(profile), kind="state", id="equator:" + check_id
        )
        file = await self.repository.call(
            "get", user_id=owner(profile), kind="file", id=stored["attachment"]["id"]
        )
        target_profile = profile if context.demo else await runtime.data.profile(recipient)
        chat = await self.telegram_chat(target_profile)
        target_file = stored.get("target_file")
        if target_file is None:
            target_file = await self.save_file(target_profile, file["name"], base64.b64decode(file["data"]))
            stored["target_file"] = target_file
            await self.repository.call(
                "put", user_id=owner(profile), kind="state", id="equator:" + check_id, payload=stored
            )
        for part in ("file", "report"):
            state = getattr(record, "delivery_" + part)
            if state == "sent":
                continue
            if state == "sending":
                await self.agent.equator.delivery(record, part, "unknown", context, runtime)
                state = "unknown"
            if state == "unknown" and not retry_unknown:
                reply.result += "\nИсход доставки неизвестен. Повтор может создать копию."
                reply.actions = [Action(label="Повторить, возможна копия", data="equator:retry:" + check_id)]
                return reply
            await self.agent.equator.claim(record, part, context, runtime, retry_unknown=retry_unknown)
            outcome = "sent"
            try:
                response = ExecutorResponse(result="Презентация к экватору") if part == "file" else note
                await self.record(
                    target_profile,
                    response,
                    channel="background",
                    ident="h4:" + owner(profile) + ":" + check_id + ":" + part,
                    attachments=[target_file] if part == "file" else [],
                )
                if chat and self.bot:
                    if part == "file":
                        await self.bot.send_document(
                            chat, BufferedInputFile(base64.b64decode(file["data"]), filename=file["name"])
                        )
                    else:
                        await report(partial(self.bot.send_message, chat), note)
            except (TelegramBadRequest, TelegramForbiddenError, ValueError):
                outcome = "failed"
            except Exception:
                outcome = "unknown"
            await self.agent.equator.delivery(record, part, outcome, context, runtime)
            if outcome != "sent":
                reply.result += "\nДоставка не подтверждена. Повтор может создать копию."
                reply.actions = [Action(label="Повторить, возможна копия", data="equator:retry:" + check_id)]
                return reply
        reply.result += "\nПрезентация и отчёт доступны наставнику."
        reply.feedback_saved = True
        return reply

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
