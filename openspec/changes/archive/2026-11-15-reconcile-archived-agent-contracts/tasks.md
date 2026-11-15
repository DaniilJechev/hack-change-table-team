## Проверка
- [x] Согласовать ранние требования с FR-AG-11/14/16 без изменения поведения.
- [x] Проверить дельту: openspec validate reconcile-archived-agent-contracts --strict.
- [x] Запустить openspec validate --all --strict: выявлены предупреждения в базовых спецификациях (длина требований и Purpose); это не пройденная строгая проверка.
- [x] Проверить обычный openspec validate --all и архивировать дельту.
