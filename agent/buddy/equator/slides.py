"""FR-H4-01: a deck → numbered slides. PDF via pypdf here; PPTX text arrives from the PowerPoint MCP."""

import io
import json
from dataclasses import dataclass

MAX_BYTES = 10 << 20
MAX_SLIDES = 30


@dataclass(frozen=True)
class Slide:
    number: int
    title: str
    text: str

    @property
    def lines(self) -> list[str]:
        return [line.strip() for line in self.text.split("\n") if line.strip()]


class DeckError(Exception):
    """Safe reason only: format, too_large, too_many, unreadable, no_text, demo_unknown, unavailable."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def detect(name: str, data: bytes) -> str:
    """Extension and file signature must agree; anything else is not checked."""
    lower = (name or "").lower()
    if lower.endswith(".pdf") and data.startswith(b"%PDF-"):
        return "pdf"
    if lower.endswith(".pptx") and data.startswith(b"PK\x03\x04"):
        return "pptx"
    raise DeckError("format")


def _finish(slides: list[Slide]) -> list[Slide]:
    if len(slides) > MAX_SLIDES:
        raise DeckError("too_many")
    if not any(slide.text.strip() for slide in slides):
        raise DeckError("no_text")
    return slides


def pdf_slides(data: bytes) -> list[Slide]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if len(data) > MAX_BYTES:
        raise DeckError("too_large")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > MAX_SLIDES:
            raise DeckError("unreadable" if reader.is_encrypted else "too_many")
        slides = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            slides.append(Slide(number, lines[0] if lines else "", "\n".join(lines)))
    except DeckError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, OSError):
        raise DeckError("unreadable") from None
    return _finish(slides)


def _text_result(result: dict) -> dict:
    try:
        value = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise DeckError("unreadable") from None
    # Upstream reports its own failures as a normal JSON body with "error".
    if not isinstance(value, dict) or "error" in value:
        raise DeckError("unreadable")
    return value


def opened(result: dict) -> int:
    count = _text_result(result).get("slide_count")
    if not isinstance(count, int):
        raise DeckError("unreadable")
    if count > MAX_SLIDES:
        raise DeckError("too_many")
    return count


def pptx_slides(result: dict) -> list[Slide]:
    """extract_presentation_text of Office-PowerPoint-MCP-Server 2.0.7."""
    value = _text_result(result)
    items = value.get("slides_text")
    if not isinstance(items, list):
        raise DeckError("unreadable")
    slides = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text_content"), dict):
            raise DeckError("unreadable")
        content = item["text_content"]
        title = (content.get("slide_title") or "").strip()
        lines = []
        for line in (content.get("all_text_combined") or "").split("\n"):
            # all_text_combined repeats the title (once as title, once as placeholder).
            if line.strip() and not (lines and line.strip() == lines[-1]):
                lines.append(line.strip())
        if title and (not lines or lines[0] != title):
            lines.insert(0, title)
        slides.append(Slide(len(slides) + 1, title or (lines[0] if lines else ""), "\n".join(lines)))
    return _finish(slides)
