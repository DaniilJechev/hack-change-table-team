"""Full offline run of the demo scenario through the real aiogram dispatcher (no Telegram, no network).

Writes the transcript used for the video script: `python scripts/demo_transcript.py`.
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from tests.test_stage_two_bot import Harness  # noqa: E402

STEPS = [
    ("Вход в демо", ["/demo"]),
    (
        "H0 · утренний бриф",
        ["/brief", "button:brief:more:", "button:brief:positive:", "Есть что по PR?", "Что нового?"],
    ),
    ("H0 · статистика и настройки", ["/stats", "/settings quiet on"]),
    ("H1 · доступы", ["Что с моими доступами?", "button:mark:", "Какие доступы нужны для ORD-118?"]),
    ("H1 · доступ появился (следующий шаг сценария)", ["/demo next"]),
    (
        "H2 · кто есть кто",
        ["Упал staging, кому писать?", "Можно написать Петру напрямую?", "На каком этапе проект?"],
    ),
    (
        "H2 · вопрос без ответа и эскалация",
        [
            "Почему у нас Kafka, а не RabbitMQ?",
            "button:esc:resp",
            "button:ans:",
            "Нужна шина событий между сервисами.",
            "button:faq:",
            "Зачем нам Kafka?",
        ],
    ),
    (
        "H3 · playbook",
        ["Что такое Zero Bug Policy?", "Что такое юниты и кластеры?", "Какой пароль от Wi-Fi в офисе?"],
    ),
    ("H0 · следующее утро", ["/demo next", "/brief"]),
    ("Роль наставника", ["/demo mentor", "/interns", "/stats"]),
]


def find(app: Harness, prefix: str) -> str:
    for message in reversed(app.session.sent):
        markup = message.reply_markup
        for row in markup.inline_keyboard if markup else []:
            for button in row:
                if (button.callback_data or "").startswith(prefix):
                    return button.callback_data
    raise AssertionError("no button " + prefix)


def render(message) -> str:
    lines = ["```", message.text, "```"]
    markup = message.reply_markup
    labels = [button.text for row in (markup.inline_keyboard if markup else []) for button in row]
    if labels:
        lines.append("Кнопки: " + " · ".join(f"[{label}]" for label in labels))
    return "\n".join(lines)


async def run() -> str:
    app = Harness()
    out = [
        "# Протокол полного прогона демо-сценария (Этап 4)",
        "",
        "Получен командой `python scripts/demo_transcript.py`: настоящий диспетчер aiogram, офлайн-сессия,",
        "`DEMO_MODE=true`, поиск по playbook в режиме `bm25`, без LLM и без внешних аккаунтов.",
        "Все данные команды синтетические (`example.invalid`); ответы H3 — по публичному AvitoTech Playbook.",
    ]
    try:
        for title, actions in STEPS:
            out += ["", f"## {title}"]
            for action in actions:
                before = len(app.session.sent)
                if action.startswith("button:"):
                    data = find(app, action.removeprefix("button:"))
                    await app.callback(data)
                    out += ["", f"**Нажата кнопка** `{data.split(':')[0]}`"]
                else:
                    await app.send(action)
                    out += ["", f"**Пользователь:** {action}"]
                for message in app.session.sent[before:]:
                    out += ["", render(message)]
    finally:
        await app.bot.session.close()
    return "\n".join(out) + "\n"


def main() -> int:
    text = asyncio.run(run())
    (ROOT / "reports" / "STAGE_4_DEMO_TRANSCRIPT.md").write_text(text, encoding="utf-8", newline="\n")
    print(
        f"Transcript: {text.count('**Пользователь:**')} messages, {text.count('**Нажата кнопка**')} buttons"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
