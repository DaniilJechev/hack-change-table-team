# Отчёты и свидетельства проверок

Индекс обновлён 15.11.2026. Отчёт подтверждает состояние своей даты и версии кода;
старое «бот остановлен», число тестов или остаток LLM-бюджета не переносится на текущую сессию.
Исторические файлы сохранены на местах, чтобы не ломать ссылки из GitHub и спецификаций.

## Начать отсюда

- [Текущее состояние и следующие работы](../docs/3_ROADMAP.md).
- [Актуализация документов и очистка: было → стало](DOCUMENTATION_CLEANUP_REPORT.md).
- [Структурный рефакторинг агента](AGENT_STRUCTURE_REFACTOR.md).
- [Каталог метрик и ограничения измерения](../docs/METRICS.md).
- [Состояние активных OpenSpec-изменений](../docs/OPENSPEC_STATUS.md).

## Качество и агент

- [Сохранение фактов и источников: сравнение 14.11](AGENT_INTEGRITY_BEFORE_AFTER.md).
- [Эксперимент с оценщиком](EVALUATOR_EXPERIMENT.md), [ручной разбор](EVALUATOR_LIVE_REVIEW.md).
- [Исправление отказов playbook](PLAYBOOK_LIVE_REFUSAL_FIX.md).
- [Трасса одного хода](AGENT_TURN_TRACE_REPORT.md).
- Полные наборы, команды и ограничения экспериментов: [evals/README.md](../evals/README.md).

## H4 и дополнительный канал

- [Завершение H4](H4_COMPLETION_REPORT.md), [backend истории H4](H4_BACKEND_COMPLETION.md).
- [Windows-клиент](WINDOWS_DESKTOP_CLIENT.md); [инструкция](../desktop/README.md).
- [Ручной сценарий H4](../docs/H4_OPERATIONS.md).

## История этапов

- [Этап 0](STAGE_0_VALIDATION.md), [этап 1](STAGE_1_EMIL_DANIIL_REPORT.md),
  [этап 2](STAGE_2_EMIL_DANIIL_REPORT.md).
- [Этап 3](STAGE_3_COMPLETION_REPORT.md), [этап 4](STAGE_4_COMPLETION_REPORT.md),
  [интеграция 11.11](MAIN_INTEGRATION_REPORT.md).
- Протоколы: [этап 3](STAGE_3_DEMO_TRANSCRIPT.md), [этап 4](STAGE_4_DEMO_TRANSCRIPT.md).
- Исторические планы разработки находятся в [plans](../plans).

PR-ревью, баг-листы и промежуточные планы в этой папке сохранены как история;
они не объявляются актуальной дорожной картой. Непроверенные ручные пункты нельзя
закрыть наличием unit-тестов. Незакоммиченный EVALUATOR_HANDOFF.md принадлежит
отдельной работе и в эту актуализацию не включается.
