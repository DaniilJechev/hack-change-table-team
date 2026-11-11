"""Split a playbook snapshot into heading-level fragments with GitHub anchor links (FR-H3-01)."""

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY = "https://github.com/avito-tech/playbook"
HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
MAX_CHARS = 1200
MIN_CHARS = 60


@dataclass(frozen=True)
class Chunk:
    id: str
    file: str
    heading: str
    url: str
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


def anchor(heading: str, seen: dict[str, int]) -> str:
    """GitHub heading slug: lower case, punctuation removed, spaces to hyphens, duplicates numbered."""
    slug = re.sub(r"[^\w\- ]", "", LINK.sub(r"\1", heading).strip().lower()).replace(" ", "-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count}"


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", LINK.sub(r"\1", text))
    text = re.sub(r"[*_`>|]+|-{3,}|:-+:?", " ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def is_contents(body: str) -> bool:
    """A table of contents is a list of in-page links and carries no answer text."""
    lines = [line for line in body.splitlines() if line.strip()]
    return bool(lines) and sum("](#" in line for line in lines) >= 0.6 * len(lines)


def pieces(text: str) -> list[str]:
    result, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        while len(paragraph) > MAX_CHARS:
            cut = paragraph.rfind(". ", 0, MAX_CHARS) + 1 or MAX_CHARS
            if current:
                result.append(current)
                current = ""
            result.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > MAX_CHARS:
            result.append(current)
            current = ""
        current = (current + "\n\n" + paragraph).strip() if paragraph else current
    if current:
        result.append(current)
    return result


def split(file: str, source: str, commit: str) -> list[Chunk]:
    sections: list[tuple[str, str, list[str]]] = []  # (heading path, anchor, body lines)
    path: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    fenced = False
    for line in source.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if match:
            level, title = len(match.group(1)), clean(match.group(2))
            path = [item for item in path if item[0] < level] + [(level, title)]
            sections.append((" › ".join(item[1] for item in path), anchor(match.group(2), seen), []))
        elif sections:
            sections[-1][2].append(line)
    chunks = []
    for heading, slug, lines in sections:
        body = "\n".join(lines)
        if is_contents(body):
            continue
        text = clean(body)
        if len(text) < MIN_CHARS:
            continue
        for number, piece in enumerate(pieces(text)):
            digest = hashlib.sha256(f"{commit}/{file}#{slug}/{number}".encode()).hexdigest()[:16]
            chunks.append(
                Chunk(
                    id=digest,
                    file=file,
                    heading=heading,
                    url=f"{REPOSITORY}/blob/{commit}/{file}#{slug}",
                    text=piece,
                )
            )
    return chunks


def split_snapshot(root: Path, commit: str) -> list[Chunk]:
    chunks = []
    for path in sorted(root.rglob("*.md"), key=lambda item: item.relative_to(root).as_posix().lower()):
        chunks.extend(split(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"), commit))
    return chunks
