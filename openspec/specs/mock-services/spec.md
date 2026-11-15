# mock-services Specification

## Purpose
TBD - created by archiving change mock-services-contract. Update Purpose after archive.

## Requirements

### Requirement: MOCK-ACCESS-1 Контракт имитации доступов
POST /api/v1/mock/access-requests SHALL принимать object_id и возвращать сохранённую
заявку со status granted/pending/denied/error, reason, error_code и simulated=true.
GET того же пути SHALL возвращать только собственные заявки. Запрещённые/невидимые
объекты MUST отклоняться до создания заявки. Повторы для объекта MUST возвращать
существующую заявку; error MAY повторно оцениваться по правилу seed.

#### Scenario: Четыре исхода
- **WHEN** разрешённый объект имеет правило seed granted, pending, denied или error
- **THEN** request_access возвращает соответствующий статус; denied содержит причину, error — безопасный код

#### Scenario: Запрет и повтор
- **WHEN** пользователь повторно запрашивает разрешённый объект или указывает запрещённый/чужой объект
- **THEN** повтор не создаёт дубликат, запрещённый/чужой объект не создаёт заявку

### Requirement: MOCK-ACCESS-2 Завершение согласования
PATCH /api/v1/mock/access-requests/{id} SHALL переводить pending в granted или denied
только для тимлида команды заявки. Отказ MUST иметь причину. Повтор того же решения
SHALL быть безопасным; изменение принятого решения MUST давать conflict.

#### Scenario: Решение тимлида
- **WHEN** тимлид завершает согласование своей команды, а стажёр повторно читает заявку
- **THEN** стажёр видит сохранённый результат; стажёр/чужая команда не могут принять решение

### Requirement: MOCK-PROGRESS-1 Трекер стажёра
GET /api/v1/mock/progress SHALL возвращать собственные пункты трекера, включая
equator_presentation («Презентация к экватору»). PUT /api/v1/mock/progress/{item}
SHALL принимать completed (boolean). Ответ SHALL включать item_id, title, completed,
completed_at, updated_at и simulated=true. Повторная отметка MUST сохранять completed_at.

#### Scenario: Отметка H4
- **WHEN** стажёр явно подтверждает отправку актуальной презентации с пройденными обязательными критериями H4 и агент вызывает mark_progress(user_id, "equator_presentation")
- **THEN** пункт означает «проверено AI-buddy»; progress(user_id) возвращает статус, повтор не меняет первое время завершения, доставка наставнику учитывается отдельно

### Requirement: MOCK-DEMO-1 Офлайн-паритет
TeamData SHALL объявлять request_access, access_requests, resolve_access_request,
progress и mark_progress. BackendData SHALL использовать REST имитаций. SeedData MUST
выполнять операции без HTTP/backend с изоляцией пользователей и демо-сессий.
В демо Miro SHALL быть pending на step_1 и granted начиная с step_2 после запроса.

#### Scenario: Miro и сброс
- **WHEN** запрошен Miro на первом шаге, затем выполнен /demo next или /demo reset
- **THEN** заявка переходит pending → granted при чтении следующего шага, а reset очищает личное состояние

### Requirement: MOCK-DB-1 Постоянное хранение имитаций
Backend SHALL реализовывать контракт MOCK-ACCESS-1/2 и MOCK-PROGRESS-1 в PostgreSQL.
Правила seed SHALL определять исход по паре команда/объект, без случайного поведения.
Seed MUST NOT стирать заявки, решения и прогресс; неверный seed MUST откатываться атомарно.
Перезапуск backend MUST сохранять статусы и первое время завершения пункта.

#### Scenario: Перезапуск и повторный импорт
- **WHEN** создана pending-заявка, тимлид выдал доступ, прогресс отмечен, затем backend перезапущен и seed импортирован повторно
- **THEN** результат granted и завершённый прогресс сохраняются

#### Scenario: Конкурентный запрос и отказ
- **WHEN** пользователь одновременно повторяет запрос, а затем тимлид завершает согласование отказом с причиной
- **THEN** существует одна заявка на объект и сохранена причина; конфликтное изменение решения отклоняется

#### Scenario: Неверный импорт
- **WHEN** правило относится к чужому/несуществующему объекту или содержит некорректный статус/код
- **THEN** импорт целиком откатывается, прежние правила и личные записи остаются
