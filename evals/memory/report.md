# Оценка памяти диалога (демо-режим, только правила)

Требования: FR-MEM-03/04/05, FR-H0-12, FR-H3-07, RT-08. LLM не вызывается; данные синтетические.
Запуск: `uv run --project agent --frozen --extra dev python evals/run_memory.py`.

- Диалогов: 23, пройдено: 23
- Ходов: 61, пройдено: 61 (100%)

| Диалог | Роль | Требование | Ходов | Результат |
|---|---|---|---|---|
| mem-01 | intern | FR-H0-12 | 5 | пройден |
| mem-02 | intern | FR-H0-12 | 4 | пройден |
| mem-03 | intern | FR-H0-12 | 3 | пройден |
| mem-04 | intern | FR-MEM-05 | 4 | пройден |
| mem-05 | mentor | FR-H0-12 | 2 | пройден |
| mem-06 | intern | FR-H3-07 | 3 | пройден |
| mem-07 | intern | FR-H3-07 | 2 | пройден |
| mem-08 | intern | FR-H3-07 | 2 | пройден |
| mem-09 | intern | FR-H3-07 | 2 | пройден |
| mem-10 | intern | RT-08 | 3 | пройден |
| mem-11 | intern | RT-08 | 2 | пройден |
| mem-12 | intern | FR-MEM-05 | 3 | пройден |
| mem-13 | intern | FR-MEM-05 | 2 | пройден |
| mem-14 | intern | FR-MEM-05 | 2 | пройден |
| mem-15 | intern | FR-MEM-03 | 3 | пройден |
| mem-16 | intern | FR-MEM-03 | 2 | пройден |
| mem-17 | intern | FR-MEM-03 | 3 | пройден |
| mem-18 | mentor | FR-MEM-03 | 3 | пройден |
| mem-19 | intern | FR-MEM-05 | 1 | пройден |
| mem-20 | intern | FR-MEM-05 | 2 | пройден |
| mem-21 | intern | FR-MEM-04 | 2 | пройден |
| mem-22 | intern | FR-MEM-04 | 3 | пройден |
| mem-23 | intern | FR-MEM-05 | 3 | пройден |

## Ошибки

Нет.
