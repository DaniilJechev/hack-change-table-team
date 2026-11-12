# И4: архивирование интеграций — 12.11.2026

База main a0dbf70. По поручению Ильи штатной командой `openspec archive --yes`
заархивированы brief-feedback-ui, github-project-source и jira-brief-changes.
Дельты перенесены в openspec/changes/archive/2026-11-12-*, требования применены к
h0-brief, github-project-source и jira-brief-changes. Автоматические заглушки Purpose
заменены описанием назначения без новых требований. Архивы Эмиля не затрагивались.

Проверки: `openspec validate --all` — 21 passed; `git diff --check` без ошибок.
Код не меняется. Перед удалением веток отдельно проверено включение их HEAD в main.
Локальные и удалённые feat/github-project-source (31c8f9c) и feat/jira-brief-changes
(15f519c) удалены; соответствующие PR #18 и #19 уже слиты. Другие ветки не удалялись.

Ограничения: новые имитации/H1 относятся к отдельным PR #23–25; этот PR не закрывает
обещанное Даниилом согласование новой H1-спецификации.
