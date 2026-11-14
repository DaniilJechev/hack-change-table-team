## ADDED Requirements

### Requirement: FR-AG-15 Читаемый трейс хода
Состояние графа SHALL содержать поле `turn` — JSON одного хода, собранный кодом из `llm_calls` и состояния: `request`, `model_input` агента, `steps`, `forced_refresh`, `other_llm_calls`, `answer`, `limits`, `fallback` и `duration_ms`. В `model_input` системный промпт, доверенный контекст, история и текущее сообщение MUST быть отдельными полями. Шаг SHALL содержать ответ модели и вызовы инструментов с аргументами, результатом и метками источников.

#### Scenario: История отделена от текущего вопроса
- **WHEN** агент отвечает на второй вопрос диалога
- **THEN** `turn.model_input.history` содержит прошлую пару реплик, а `turn.model_input.current_message` — только новый вопрос

#### Scenario: Шаг с инструментом
- **WHEN** модель вызвала `brief_now`, а затем ответила текстом
- **THEN** `turn.steps` содержит два шага: первый с вызовом `brief_now`, его результатом и метками `S#`, второй — с текстом ответа, а `turn.limits.tool_calls` равно 1

#### Scenario: Запасной путь
- **WHEN** обращение агента к модели упало и ответил Router
- **THEN** `turn.fallback` равно true, `turn.request.path` равно `router`, а промпт Router есть в `turn.other_llm_calls`

#### Scenario: Ход без LLM
- **WHEN** пользователь отправил `/brief`
- **THEN** в `turn` нет `model_input` и `steps`, а `turn.answer` содержит текст и источники ответа

### Requirement: FR-AG-16 Состав трейса хода
Поле `prompt` MUST NOT храниться в состоянии: последний запрос к модели виден в `llm_calls`, который остаётся сырым журналом FR-AG-10. `turn` MUST NOT содержать ключ API, `entities`, `brief_id` и данные кнопок (`data`). Контракт `ExecutorResponse` и поведение бота SHALL оставаться прежними.

#### Scenario: Внутренние поля не попадают в трейс
- **WHEN** ответ содержит кнопки с данными и `entities`
- **THEN** `turn.answer.buttons` содержит только подписи, а в `turn` нет `entities`, `brief_id` и ключа API
