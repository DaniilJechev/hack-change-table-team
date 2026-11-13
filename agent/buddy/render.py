"""FR-UI-01/02: Telegram markup is built by code. Text is escaped; only code adds tags.

Executors choose what is secondary (`ExecutorResponse.folded`); a long answer without such blocks
is cut at a line boundary. FR-UI-03: links are code-made footnotes [n] at the line that uses them.
"""

import html
import re

LIMIT = 4096  # Telegram message length
FOLD_LINES, FOLD_CHARS = 12, 900  # a longer answer without folded blocks gets its tail folded
KEEP_LINES = 6
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
MARK = re.compile(r"([ \t]*)\[(\d+)\]")
NUMBERED = re.compile(r"\d+\. ")


def plain(text: str) -> str:
    """B-07: Markdown emphasis from an LLM answer is not shown as asterisks."""
    return BOLD.sub(lambda match: match.group(1) or match.group(2), text)


def cite(result: str, folded: list[str], links: list[str]) -> tuple[str, list[str], list[str]]:
    """FR-UI-03: footnotes [n] by the links, one number per URL. Numbered lines that match the links
    1:1 (the result first, then folded blocks) get their own; otherwise the last line of the result
    gets them all. A text that already has footnotes (the agent's) is kept."""
    if not links or any(MARK.search(text) for text in [result, *folded]):
        return result, folded, links
    unique = list(dict.fromkeys(links))
    numbers = [unique.index(link) + 1 for link in links]
    lines = [text.split("\n") for text in [result, *folded]]
    for blocks in (lines[:1], lines[1:]):
        rows = [(block, i) for block in blocks for i, line in enumerate(block) if NUMBERED.match(line)]
        if len(rows) == len(links):
            for (block, i), number in zip(rows, numbers, strict=True):
                block[i] += f" [{number}]"
            break
    else:
        head = lines[0]
        last = max((i for i, line in enumerate(head) if line.strip()), default=0)
        head[last] += "".join(f" [{number}]" for number in range(1, len(unique) + 1))
    texts = ["\n".join(block) for block in lines]
    return texts[0], texts[1:], unique


def _marks(text: str, links: list[str], html_mode: bool) -> str:
    """A footnote without a link is dropped: a URL is never invented."""

    def link(match: re.Match) -> str:
        number = int(match.group(2))
        if not 1 <= number <= len(links):
            return ""
        if not html_mode:
            return match.group(0)
        return f'{match.group(1)}<a href="{html.escape(links[number - 1], quote=True)}">[{number}]</a>'

    return MARK.sub(link, text)


def _inline(text: str, links: list[str]) -> str:
    text = BOLD.sub(
        lambda match: f"<b>{match.group(1) or match.group(2)}</b>", html.escape(text, quote=False)
    )
    return _marks(text, links, True)


def _quote(body: str) -> str:
    return f"<blockquote expandable>{body}</blockquote>"


def split_long(text: str) -> tuple[str, str]:
    """A long text keeps its first lines; the tail is cut only between lines, never mid-sentence."""
    lines = text.split("\n")
    if (len(lines) <= FOLD_LINES and len(text) <= FOLD_CHARS) or len(lines) <= KEEP_LINES:
        return text, ""
    return "\n".join(lines[:KEEP_LINES]), "\n".join(lines[KEEP_LINES:])


def to_plain(result: str, folded: list[str], links: list[str], prefix: str = "") -> str:
    """Telegram rejected the HTML: the same footnotes and a «[n] URL» list, at most LIMIT characters."""
    result, folded, links = cite(result, folded, links)
    text = plain("\n".join([prefix + result, *(block for block in folded if block.strip())]))
    text = _marks(text, links, False)
    sources = "".join(f"\n[{number}] {link}" for number, link in enumerate(links, 1))
    if len(text) + len(sources) > LIMIT:
        text = text[: LIMIT - len(sources)].rsplit("\n", 1)[0]
    return text + sources


def to_html(result: str, folded: list[str], links: list[str], prefix: str = "") -> str:
    result, folded, links = cite(result, folded, links)
    head, tail = (result, "") if folded else split_long(result)
    parts = [_inline(prefix + head, links)]
    parts += [_quote(_inline(block, links)) for block in [*folded, tail] if block.strip()]
    return "\n".join(parts)
