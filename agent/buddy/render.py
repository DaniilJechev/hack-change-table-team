"""FR-UI-01/02: Telegram markup is built by code. Text is escaped; only code adds tags.

Executors choose what is secondary (`ExecutorResponse.folded`); a long answer without such blocks
is cut at a line boundary. Links stay code-made: a long «Источники» list is folded too.
"""

import html
import re

FOLD_LINKS = 3  # more links than this — the source list is folded
FOLD_LINES, FOLD_CHARS = 12, 900  # a longer answer without folded blocks gets its tail folded
KEEP_LINES = 6
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def plain(text: str) -> str:
    """B-07: Markdown emphasis from an LLM answer is not shown as asterisks."""
    return BOLD.sub(lambda match: match.group(1) or match.group(2), text)


def _inline(text: str) -> str:
    return BOLD.sub(
        lambda match: f"<b>{match.group(1) or match.group(2)}</b>", html.escape(text, quote=False)
    )


def _quote(body: str) -> str:
    return f"<blockquote expandable>{body}</blockquote>"


def split_long(text: str) -> tuple[str, str]:
    """A long text keeps its first lines; the tail is cut only between lines, never mid-sentence."""
    lines = text.split("\n")
    if (len(lines) <= FOLD_LINES and len(text) <= FOLD_CHARS) or len(lines) <= KEEP_LINES:
        return text, ""
    return "\n".join(lines[:KEEP_LINES]), "\n".join(lines[KEEP_LINES:])


def links_block(links: list[str], html_mode: bool) -> str:
    links = list(dict.fromkeys(links))
    if not links:
        return ""
    if not html_mode:
        return "\nИсточники:\n" + "\n".join(links)
    body = "\n".join(html.escape(link) for link in links)
    return "\nИсточники:\n" + (_quote(body) if len(links) > FOLD_LINKS else body)


def to_plain(result: str, folded: list[str], links: list[str]) -> str:
    return plain("\n".join([result, *(block for block in folded if block.strip())])) + links_block(
        links, False
    )


def to_html(result: str, folded: list[str], links: list[str], prefix: str = "") -> str:
    head, tail = (result, "") if folded else split_long(result)
    parts = [_inline(prefix + head)]
    parts += [_quote(_inline(block)) for block in [*folded, tail] if block.strip()]
    return "\n".join(parts) + links_block(links, True)
