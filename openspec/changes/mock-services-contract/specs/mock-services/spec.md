## ADDED Requirements

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
- **WHEN** агент вызывает mark_progress(user_id, "equator_presentation") после проверки и отправки наставнику
- **THEN** пункт отмечается; progress(user_id) возвращает его статус, повтор не меняет первое время завершения

### Requirement: MOCK-DEMO-1 Офлайн-паритет
TeamData SHALL объявлять request_access, access_requests, resolve_access_request,
progress и mark_progress. BackendData SHALL использовать REST имитаций. SeedData MUST
выполнять операции без HTTP/backend с изоляцией пользователей и демо-сессий.
В демо Miro SHALL быть pending на step_1 и granted начиная с step_2 после запроса.

#### Scenario: Miro и сброс
- **WHEN** запрошен Miro на первом шаге, затем выполнен /demo next или /demo reset
- **THEN** заявка переходит pending → granted при чтении следующего шага, а reset очищает личное состояние
