## 1. Реализация
- [x] 1.1 Проставить и отобразить кликабельные сноски из списка источников.
- [x] 1.2 Убрать отдельный список источников, сохранить открытие через main process.

## 2. Проверка
- [x] 2.1 Проверить нумерацию, дополнительные блоки, Markdown и недопустимые ссылки.
- [x] 2.2 Выполнить тесты, сборку desktop и валидацию OpenSpec.

Проверено: `npm test` — 17 тестов; `npm run build`, `npm run format:check`,
`openspec validate --all` — 33 спецификации, `openspec validate desktop-inline-citations --strict`.
