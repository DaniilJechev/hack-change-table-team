## ADDED Requirements

### Requirement: FR-AG-14 Один узел-исполнитель
Граф SHALL содержать один узел-исполнитель `execute`, вызывающий `Registry.execute` по кейсу решения; отдельных узлов на семейства кейсов MUST NOT быть. Семейство кейса (`brief`, `playbook`, `team`, `access`, `template`) MUST определяться кодом и записываться в `trace` и в поле `node` обращений к LLM, сделанных узлом. Переход к `execute` MUST выбираться кодом по наличию решения, белые списки инструментов кейсов и запасные варианты без LLM не меняются.

#### Scenario: Один узел вместо пяти
- **WHEN** граф собран
- **THEN** в нём есть узел `execute`, а узлов `brief`, `playbook`, `team`, `access`, `template` нет

#### Scenario: Семейство в трассе
- **WHEN** Router вернул `team_q7`
- **THEN** `trace` равен `["team"]`, а обращения к LLM узла `execute` помечены `node: "team"`

#### Scenario: Прямой триггер
- **WHEN** планировщик запускает `brief_render` с готовым решением
- **THEN** граф передаёт управление узлу `execute` без вызова Router
