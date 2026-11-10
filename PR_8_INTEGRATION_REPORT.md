# Интеграция PR №8 (backend Этапа 2) с main

Дата: 10.11.2026.

## Проблема
PR №8 Ильи (`feat/backend-stage-2`: `buddy-mcp`, REST наших данных, загрузка seed) создан до слияния PR №6 Эмиля и Даниила. Git-конфликта не было, но после PR №6 в `main` лежат seed-файлы в другом формате, а строгий загрузчик Ильи (`yaml KnownFields`) их отвергал:

| Файл | Ошибка загрузки |
|---|---|
| `seed/team_cards/demo-orders.yaml` | `seed.card.links: expected list` — ссылки были словарём |
| `seed/access_catalog.yaml` | `seed: invalid YAML schema` — поля `role`, `object_id`, `title`, `external_id`, `instructions`, `allowed`, `forbidden_objects` неизвестны backend |

Слияние PR №8 как есть ломало бы `backend seed` и подъём проекта. CI PR №8 этого не видел, потому что запускался на `main` до PR №6.

## Решение
По решению команды за основу взят код Ильи без изменений; данные и код агента приведены к его формату.

**Карточка команды** — `links` стал списком:
```yaml
links:
- service: github
  url: https://example.invalid/github/...
```

**Каталог доступов** — формат `backend/internal/store.CatalogObject`:
```yaml
objects:
- id: demo-orders-github
  instrument: github
  name: Репозиторий orders-onboarding
  url: https://example.invalid/...
  request_url: https://example.invalid/team/demo-orders/requests/github
  owner_id: demo-oleg
  roles: [intern, mentor, teamlead]
  forbidden: false
- id: demo-orders-production      # запрещённый объект — в том же списке
  forbidden: true
  reason: Этот учебный объект запрещён роли Backend-стажёр.
  roles: [intern]
```
Убраны поля, которых нет в backend: `role` (верхний уровень), `external_id`, `instructions`, отдельный список `forbidden_objects`. Внешние идентификаторы (репозиторий, ID чата) для демо по-прежнему берутся из сценария `seed/day_scenario.json` и фикстур.

## Изменённые файлы
| Файл | Изменение |
|---|---|
| `seed/build_stage_two.py` | Генератор выдаёт карточку и каталог в формате backend |
| `seed/team_cards/demo-orders.yaml`, `seed/access_catalog.yaml` | Перегенерированы |
| `agent/buddy/demo/contracts.py` | Pydantic-контракты `Link`, `AccessObject`, `AccessCatalog` повторяют формат backend; сверка каталога со сценарием — по URL объектов вместо `external_id`; новые проверки: дубли ролей и ссылок, `request_url` обязателен для незапрещённых объектов, роль `intern` описана у каждого объекта |
| `agent/tests/test_stage_two_demo.py` | Тесты под новый формат + новые негативные случаи (`link`, `request`, `roles`) |

Код backend Ильи (PR №8) не менялся.

## Проверки
| Проверка | Результат |
|---|---|
| Загрузка seed из `main` кодом Ильи (`SeedData`) | Проходит разбор и валидацию всех трёх файлов |
| `go test ./...`, `go vet`, `gofmt` (Go 1.24) | OK |
| Python: `pytest agent/tests` | 265 passed, покрытие агента 99 % |
| `ruff check`, `ruff format --check` | OK |
| CI PR (tests, compose с seed и `buddy-check`, mcp) | См. PR |

## Итог
После слияния в `main` находятся: backend Этапа 2 (`buddy-mcp`, REST, миграция и загрузка seed) и агент Этапа 2 (Router, Executors, демо-режим) с совместимыми данными. PR №8 закрывается этим слиянием, так как его коммиты входят в интеграционную ветку.
