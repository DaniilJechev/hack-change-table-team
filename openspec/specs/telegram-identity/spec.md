# telegram-identity Specification

## Purpose
Приватная привязка Telegram к backend Этапа 1, безопасный повторный вход и совместимость агента с готовыми MCP runtimes.

## Requirements

### Requirement: ST1-BIND-1 Приватная привязка
При настроенных BACKEND_URL и BUDDY_SERVICE_KEY бот MUST передавать код /start в POST /api/v1/auth/bind, а telegram_user_id и telegram_chat_id MUST брать только из Telegram Message. Бот MUST отклонять привязку в группах и от сообщений без пользователя. Роль и команда MUST браться только из ответа backend. Код, ключ и тело ошибки MUST NOT попадать в ответ или журнал; запрос ограничен таймаутом 5 секунд, редиректы запрещены.

#### Scenario: Успешная привязка
- **WHEN** личное сообщение содержит действительный код
- **THEN** backend привязывает пользователя, бот показывает русское меню и серверную роль

#### Scenario: Повторный вход
- **WHEN** уже привязанный пользователь отправляет /start без кода
- **THEN** профиль загружается GET /api/v1/auth/telegram/{id} без новой привязки

#### Scenario: Требуется приглашение
- **WHEN** профиль не найден при /start без кода
- **THEN** бот просит персональную ссылку приглашения

#### Scenario: Ошибка привязки
- **WHEN** backend возвращает 400, 401, 409, 503, неверный профиль или не отвечает
- **THEN** бот отвечает безопасным русским шаблоном без кода, ключа и внутренних ошибок

#### Scenario: Группа
- **WHEN** /start приходит из группы или без подтверждённого Telegram-пользователя
- **THEN** backend не вызывается и предлагается личный чат

### Requirement: ST1-MCP-COMPAT-1 Совместимость runtime
MCP-клиент MUST читать настройки из .env и разрешённых переменных процесса с приоритетом процесса, понимать MCP_ATLASSIAN_URL и MCP_CALENDAR_URL, поддерживать Basic Jira и read-only stdio GitHub. Секреты авторизации MUST быть скрыты в repr и исключены из записи фикстур.

#### Scenario: Настройки Compose
- **WHEN** переданы переменные runtime Ильи
- **THEN** Jira использует Basic, Calendar HTTP, GitHub штатный wrapper только чтения
