## ADDED Requirements

### Requirement: DESKTOP-08 Inline source citations
AvitoCode SHALL render `[n]` in assistant replies and secondary blocks as links
from `response.links`, opened through the external-link handler, without a
separate source list. It SHALL preserve existing numbering and generate missing
markers as in Telegram. Unsafe or missing URLs MUST NOT become clickable.
Markdown links, code and user messages SHALL remain intact; raw HTML MUST NOT execute.

#### Scenario: Existing source markers
- **WHEN** an assistant reply or secondary block contains `[1]` with a source URL
- **THEN** clicking `[1]` opens that URL through the external-link handler without expanding a source list

#### Scenario: Reply without markers
- **WHEN** an assistant reply has sources but no markers
- **THEN** numbered rows matching sources one-to-one receive corresponding markers; otherwise markers follow the last nonempty reply line, with duplicate URLs sharing one number

#### Scenario: Invalid source and preserved Markdown
- **WHEN** a reply contains a marker without a safe source, existing Markdown links, code or raw HTML
- **THEN** no unsafe source link is created, Markdown links and code retain their meaning and raw HTML does not execute

#### Scenario: User text
- **WHEN** a user message contains `[1]`
- **THEN** it remains user text without an injected source link
