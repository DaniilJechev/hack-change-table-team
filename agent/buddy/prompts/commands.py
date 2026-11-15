"""Trusted product help shared by prompts and the no-LLM fallback."""

import re

COMMANDS = {
    "start": "Привязка по персональному коду; без кода показывает профиль.",
    "help": "Возможности бота. Вопросы о команде и playbook можно писать обычным текстом.",
    "brief": "Личный бриф: изменения PR, задач Jira и встреч.",
    "access": "Проверка доступов; заявка подаётся отдельной кнопкой в имитацию сервиса доступов.",
    "interns": "Сводка стажёров, только для наставника и тимлида.",
    "stats": "Статистика брифов и взаимодействий с ними.",
    "settings": (
        "Настройки брифа: /settings показывает текущие значения; /settings brief HH:MM задаёт время, "
        "например /settings brief 09:00; /settings quiet on|off включает/выключает тихий режим. "
        "При quiet on в день без изменений бриф не приходит."
    ),
    "reset": "Очищает память диалога; не удаляет профиль и продуктовые данные.",
    "teamcard": "Загрузка YAML-карточки команды, только наставник или тимлид.",
    "demo": "Синтетический сценарий: next — следующий шаг, reset — сначала, off "
    "— выход; mentor/intern — роли.",
}
GUIDE = (
    "Команды бота:\n"
    + "\n".join(f"/{name}: {help}" for name, help in COMMANDS.items())
    + (
        "\nПрезентацию PDF/PPTX отправляют файлом: проверяется текст, файл не редактируется. "
        "Вопрос о команде — справка, не её запуск. Не утверждай, что настройки изменены или известны, "
        "если их не читал. /ask — только устаревшая совместимость, не предлагай её."
    )
)
QUESTION = re.compile(
    r"что (?:делает|такое|значит|за)|за что|как (?:работает|пользоваться|использовать|настроить)"
    r"|объясни|поясни|зачем|расскажи про",
    re.I,
)
COMMAND = re.compile(r"/([a-z]+)(?:@[a-z0-9_]+)?\b", re.I)


def referenced_command(text: str) -> str | None:
    """Only explicit explanatory questions; direct commands keep their existing handlers."""
    if text.lstrip().startswith("/") or not QUESTION.search(text):
        return None
    match = COMMAND.search(text)
    return match[1].lower() if match and match[1].lower() in COMMANDS else None
