# /// script
# requires-python = ">=3.12"
# dependencies = ["python-pptx==1.0.2", "reportlab==4.4.3"]
# ///
"""Build synthetic H4 training decks (good/bad) as .pptx and .pdf.

Not an Avito template: the six-section structure is invented by the team.
Usage: uv run --script seed/build_equator_samples.py [--font path/to/cyrillic.ttf]
"""

import argparse
from pathlib import Path

from pptx import Presentation
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "equator"
NOTE = "Учебная презентация (синтетика), не шаблон Авито."

GOOD = [
    (
        "Экваторная встреча",
        [
            "Даниил, стажёр команды «Заказы»",
            "Наставник: Анна",
            "Стажировка: сентябрь – ноябрь 2026",
        ],
    ),
    ("Цели стажировки", ["Разобраться в сервисе заказов", "Закрыть задачи эпика фильтров ORD-100"]),
    (
        "Выполненные задачи",
        [
            "ORD-115 — готово: валидация параметров фильтра",
            "ORD-118 — в работе: фильтр по статусу заказа",
            "ORD-121 — в планах: учебный клиент Kafka",
        ],
    ),
    ("Результаты", ["Фильтр по статусу ускорил поиск заказов на 30 %", "Покрытие тестами модуля — 85 %"]),
    (
        "Сложности и чему научился",
        ["Долго разбирался с миграциями", "Вывод: сначала читать playbook команды"],
    ),
    ("Планы до конца стажировки", ["Довести ORD-118 до релиза", "Подготовить доклад на демо команды"]),
]

BAD = [
    ("Привет!", ["Даниил", "Команда «Заказы»"]),
    ("Цели", ["Научиться всему"]),
    (
        "Что сделано",
        [
            "ORD-118 — в работе: фильтр по статусу заказа",
            "ORD-121 — готово: учебный клиент Kafka",
            "ORD-999 — сделано: ускорение поиска",
        ],
    ),
    ("Трудности", ["Было сложно"]),
    ("Спасибо за внимание", ["Вопросы?"]),
]


def build_pptx(slides, path: Path) -> None:
    deck = Presentation()
    for number, (title, lines) in enumerate(slides):
        layout = deck.slide_layouts[0 if number == 0 else 1]
        slide = deck.slides.add_slide(layout)
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = lines[0]
        for line in lines[1:]:
            body.add_paragraph().text = line
    deck.core_properties.comments = NOTE
    deck.save(path)


def build_pdf(slides, path: Path, font: Path) -> None:
    pdfmetrics.registerFont(TTFont("Body", str(font)))
    size = landscape(A4)
    page = canvas.Canvas(str(path), pagesize=size, invariant=1)
    page.setTitle(NOTE)
    for title, lines in slides:
        page.setFont("Body", 30)
        page.drawString(60, size[1] - 90, title)
        page.setFont("Body", 18)
        for index, line in enumerate(lines):
            page.drawString(70, size[1] - 160 - index * 34, line)
        page.showPage()
    page.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/arial.ttf"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, slides in (("good", GOOD), ("bad", BAD)):
        build_pptx(slides, OUT / f"{name}.pptx")
        build_pdf(slides, OUT / f"{name}.pdf", args.font)


if __name__ == "__main__":
    main()
