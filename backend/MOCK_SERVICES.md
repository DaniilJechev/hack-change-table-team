# Контракт И1: имитации сервисов доступов и прогресса

Согласован по поручению Ильи от 12.11; имитации явно отделены префиксом `/api/v1/mock`.
В реальные GitHub/Jira/Miro/Calendar/Telegram ничего не записывается.

И1 закрепляет контракт и слой данных; И3 реализует Go-маршруты и PostgreSQL
(миграция 00006). В демо операции выполняет SeedData без HTTP и backend.
Менять файлы `agent/buddy/data/*` для подключения H4 не нужно.

## Методы для Эмиля

```python
items = await runtime.data.progress(context.user_id)
result = await runtime.data.mark_progress(context.user_id, "equator_presentation")
assert result["completed"] is True
# Вызывать после пройденных обязательных критериев и успешной отправки наставнику.
```

Пункт `equator_presentation`, название «Презентация к экватору», отмечается как
«презентация проверена AI-buddy». Поля ответа: `item_id`, `title`, `completed` (bool),
`completed_at` (RFC3339 либо null), `updated_at` (RFC3339 либо null), `simulated=true`.
`progress()` возвращает список пунктов; даже до первой записи пункт присутствует
с `completed=false`. Повтор `mark_progress` сохраняет первое время завершения.
Для явного сброса: `mark_progress(user_id, item_id, completed=False)`.

Для H1: `request_access(user_id, object_id)` → заявка;
`access_requests(user_id)` → список своих заявок;
`resolve_access_request(lead_id, request_id, status, reason="")` → решение тимлида.
Последний метод не включается в действия стажёра и нужен для завершения имитации.

Заявка: `id`, `user_id`, `team_id`, `object_id`, `status`, `reason`, `error_code`,
`created_at`, `updated_at`, `simulated=true`. Статусы: `granted`, `pending`, `denied`,
`error`. Отказ содержит `reason`; технический исход `error` содержит один из кодов
`service_unavailable`, `service_unconfigured`, `rate_limited`. Это штатный результат
имитации с HTTP 200, который сохраняется и показывается ботом по шаблону.
Сбой самого backend — `DataError("unavailable")`, отдельно от сохранённого исхода.

## REST и права

Все операции требуют `X-Buddy-Key` и `X-Buddy-User`; команда/роль берутся из БД.

| Метод | Путь | Тело / результат |
|---|---|---|
| POST | `/api/v1/mock/access-requests` | `{object_id}` → заявка |
| GET | `/api/v1/mock/access-requests` | `{items: [заявка]}` — только свои |
| PATCH | `/api/v1/mock/access-requests/{id}` | `{status: granted|denied, reason}` → заявка |
| GET | `/api/v1/mock/progress` | `{items: [пункт]}` — только свои |
| PUT | `/api/v1/mock/progress/{item}` | `{completed: bool}` → пункт |

PATCH разрешён только тимлиду команды заявки; можно завершить только pending,
повтор того же решения безопасен. Изменить уже принятое решение нельзя (409).
Отказ без причины — 400. Невидимый объект/чужая команда — 404; запрещённый объект —
403, заявка не создаётся. Технические ошибки HTTP используют существующий
`{error: {code, message}}`; входные поля user_id/team_id/role отклоняются.

Одна заявка на пару пользователь/объект. Pending/granted/denied возвращаются повторно;
error можно повторить после исправления правила. Решение, принятое через PATCH,
повторный seed не стирает. Прогресс и заявки переживают перезапуск backend/PostgreSQL
при сохранении тома БД.

## Запуск и завершение согласования вне демо

`make up`, затем `make seed`: backend CLI читает `seed/mock_services.json` вместе с
пользователями и каталогом в одной транзакции. Для другого файла можно задать
`SEED_MOCK_SERVICES_PATH` процессу `backend seed`. Если файл отсутствует по стандартному
пути, прежние установки продолжают работать; явно заданный отсутствующий файл — ошибка.
Импорт обновляет только правила, а не исходы уже принятых решений.

Тимлид может завершить pending-заявку вручную через REST. Например, для тестового
request_id из ответа стажёру и внутреннего user_id тимлида:

```sh
curl --fail-with-body -X PATCH "http://localhost:8080/api/v1/mock/access-requests/$REQUEST_ID" \
  -H "X-Buddy-Key: $BUDDY_SERVICE_KEY" -H 'X-Buddy-User: demo-oleg' \
  -H 'Content-Type: application/json' -d '{"status":"granted","reason":"Учебное согласование"}'
```

Для отказа — `status=denied` и непустая причина. Секретные переменные берутся из
локального окружения, не из примера. Чтение GET /mock/access-requests по-прежнему
возвращает только собственные заявки, в том числе для тимлида: массового просмотра
персональных заявок команды не добавлено. Отдельной админ-панели нет.

## Правила и демо

`seed/mock_services.json` — синтетические воспроизводимые правила по команде и объекту.
Без правила результат `error/service_unconfigured`; запреты каталога приоритетнее правил.
В live `demo_steps` игнорируется, pending завершает тимлид через PATCH.
В демо Miro: step_1 pending, step_2/step_3 granted. Переход шага меняет только
существующие pending-заявки; `/demo reset` сбрасывает личное состояние сессии.
Источник копируется в JSON-пакет демо командой `seed/build_stage_two.py`.
