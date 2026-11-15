# Статус OpenSpec

Срез 15.11.2026. По поручению пользователя закрыты все девять изменений,
которые предыдущий аудит оставил активными. Активных изменений нет.
Дельты применены к базовым спецификациям командой openspec archive <имя> --yes
в порядке зависимостей. Код и поведение приложения в этой работе не менялись.

Архивирование закрывает пакет изменений, но не подтверждает прохождение ручной
приёмки или успешность качества модели. Исторические незавершённые пункты в tasks.md
сохранены без галочек; остаток работ перечислен ниже. Поручение пользователя
не выдаётся за отдельное согласование с Даниилом или Ильёй.

## Закрыто 15.11.2026

Все ссылки ведут в [архив изменений](../openspec/changes/archive).

| Изменение | Результат закрытия | Основание и ограничения |
|---|---|---|
| [stage-4-langgraph-memory](../openspec/changes/archive/2026-11-15-stage-4-langgraph-memory) | Создана context-memory, дополнены agent-foundation, H0, H3 и router | [Отчёт памяти](../reports/STAGE_4_MEMORY_REPORT.md); новые live-проверки не выполнялись |
| [agent-orchestrator](../openspec/changes/archive/2026-11-15-agent-orchestrator) | Создана agent-orchestrator, дополнена context-memory | Реализация и прежний [отчёт evals](../evals/agent/report.json); повтор после стилевых изменений отложен |
| [single-executor-node](../openspec/changes/archive/2026-11-15-single-executor-node) | Добавлен FR-AG-14: один execute | Есть тесты графа; ручная Studio-приёмка отложена |
| [agent-turn-trace](../openspec/changes/archive/2026-11-15-agent-turn-trace) | Добавлены FR-AG-15/16 и agent-traces | [Отчёт трейсов](../reports/AGENT_TURN_TRACE_REPORT.md) |
| [live-run-fixes](../openspec/changes/archive/2026-11-15-live-run-fixes) | Обновлены H0, H2, H3, router; создана telegram-format | [История багов](../reports/LIVE_RUN_BUGS.md) и тесты; повторный Telegram-прогон отложен |
| [telegram-citations](../openspec/changes/archive/2026-11-15-telegram-citations) | Сноски FR-UI-03, обновлён FR-UI-02, удалён FR-UI-01 | Применено после live-run-fixes; ручная Telegram-приёмка отложена |
| [evaluator-quality](../openspec/changes/archive/2026-11-15-evaluator-quality) | Создана agent-evaluation; закрыт первый этап оценщика | [Живой аудит](../reports/EVALUATOR_LIVE_REVIEW.md); следующие этапы не объявляются выполненными, качество исходного прогона не прошло порог |
| [windows-desktop-client](../openspec/changes/archive/2026-11-15-windows-desktop-client) | Создана desktop-client | [Отчёт клиента](../reports/WINDOWS_DESKTOP_CLIENT.md); ручная приёмка установленного приложения отложена |
| [desktop-github-releases](../openspec/changes/archive/2026-11-15-desktop-github-releases) | Создана desktop-release | Сверены CI, проверка тега/версии и release-тесты; реальный релиз в этой работе не публиковался |

Дополнительно архивирована служебная дельта
[reconcile-archived-agent-contracts](../openspec/changes/archive/2026-11-15-reconcile-archived-agent-contracts).
Она согласует ранние ST4-GRAPH-1, FR-MEM-05, FR-AG-02 и FR-AG-10 с уже принятыми
FR-AG-11/14/16: сначала агент, один execute, turn вместо prompt. Это устранение
противоречий между последовательными дельтами без нового поведения.

Предыдущий аудит уже закрыл mock-services-contract, mock-services-backend,
h1-mock-access-request, agent-answer-integrity и playbook-live-refusal.
Они остаются в том же архиве; новые проверки их реализации здесь не заявляются.

## Отложенные проверки и развитие

| Работа | Откуда перенесена | Что требуется |
|---|---|---|
| Повторные LLM-evals памяти, Router и стиля агента | stage-4-langgraph-memory §5.3, agent-orchestrator §7.4 | Датированный прогон с моделью и настройками; старые отчёты не подтверждают новые промпты |
| LangGraph Studio | single-executor-node §3.2 | Ручной прогон запросов и просмотр графа/трейсов |
| Повторный Telegram-прогон | live-run-fixes §5.2/6.7, telegram-citations §3.2 | Бриф, команда, доступы, составной вопрос playbook; обновить статусы багов по фактическому результату |
| Кейс «ценности и миссия» в H3-evals | live-run-fixes §1.5 | Отдельно проверить наличие и покрытие в наборе H3; закрытие архива не является выполнением пункта |
| Развитие оценщика | evaluator-quality, следующие этапы | Полные H0/H1/H4 с состояниями и ошибками доставки, независимые вопросы, повторы, ручная разметка и калибровка семантического судьи |
| Windows-приёмка | windows-desktop-client §3.4 | Установленный клиент: tray, toast, DPI, DPAPI и login |
| Командное согласование | Исторические пункты согласования в архивных tasks.md | При необходимости получить отдельное подтверждение команды; не считать его состоявшимся по этому архивированию |

Пункт live-run-fixes §8.4 о заявке H1 уже реализован отдельным архивированным
[h1-mock-access-request](../openspec/changes/archive/2026-11-15-h1-mock-access-request).
Порядок архивирования и добавление уточнений контракта закрыты этой работой.
Отложенная приёмка также отражена в [roadmap](3_ROADMAP.md#2-что-ещё-требуется-подтвердить).

## Проверка закрытия

- openspec list: активных изменений нет.
- openspec validate --all: базовые спецификации проходят, остаются предупреждения о формате.
- Строгая проверка служебной дельты прошла. Общий режим --strict выявил предупреждения
  о длинных требованиях, старых Purpose-заглушках и RFC 2119; успешным он не заявляется.
- Ссылки на перенесённый H4_OPERATIONS.md обновлены. Документ сохранён в
  [архиве документации](archive/2026-11-15/H4_OPERATIONS.md).
- Приложение и стек не запускались; новые live-evals и ручная приёмка не проводились.

## Новые метрики

[METRICS.md](METRICS.md) — согласованный каталог и план, не реализованная телеметрия.
Перед кодом OUT-01…06 нужна отдельная дельта: схема событий, problem_id, идемпотентность,
права, demo/live и сценарии знаменателей/неизвестного исхода. Закрытие старых изменений
не добавляет эти требования как уже выполненные.
