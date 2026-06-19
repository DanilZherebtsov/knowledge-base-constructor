# Каталог механик конструктора

Реестр отстёгиваемых механик. Конструктор читает его, чтобы знать, какие механики существуют, что каждая трогает и от чего зависит. Манифест пресета (`presets/<class>.md`) перечисляет выбранные механики в поле `mechanics:`; сборщик ([ASSEMBLY.md](ASSEMBLY.md)) для каждой выполняет её `mechanics/<имя>/_about.md`.

| Механика | Что делает | Слоты | Ключевые файлы | Зависит от | Кто использует |
|---|---|---|---|---|---|
| **roles** | Роль = линза-срез над одной общей викой по префиксам/тегам; «создай роль», «работаем в роли». Опциональна, активируется в любом классе и на любом этапе. | ROLES-FILL | `methodology/roles.md`, `roles/_шаблон.md`, раздел «Роли» в CLAUDE.md, bootstrap | ingest, lifecycle-файл класса | business |
| **spec-lifecycle** | Единица работы для проектов с кодом: спека/спринт в `specs/`, статус во frontmatter; закрытие = ADR + синтез по всем типам вики. | S6, S4, S2, S5 | `methodology/spec-lifecycle.md`, `specs/`, CLAUDE.md, STATE.md | S4 (слой `specs/`), ingest, state-rules | saas |
| **question-lifecycle** | Единица работы для research: исследовательский вопрос Q-NNN, поток STATE → output → wiki. | S6, S5, S2 | `methodology/question-lifecycle.md`, CLAUDE.md, STATE.md | **claim-graph** (обяз.); roles (опц.) | research |
| **decision-lifecycle** | Единица работы для business: решение; закрытие = `decision-closed` + ADR + каскад в `entities/`/`principles/`. | S6 | `methodology/decision-lifecycle.md`, CLAUDE.md | state-rules | business |
| **claim-graph** | Единица знания = claim; граф `supports/contradicts/refines`; обязательная локализация цитат. | S2, S3, S7, KNOWLEDGE-UNIT, DOMAIN-LINT | `page-conventions.md`, `ingest.md`, `lint.md`, `query.md`, `index-log-format.md`, CLAUDE.md | — (конфликт за центральный тип) | research |

## Правила композиции

- **Эксклюзивность центрального типа.** `claim-graph` (тип `claims/`), `spec-lifecycle` (тип `architecture/`) и `decision-lifecycle` (тип `entities/`) каждая претендует на свой центральный тип. В одном проекте центральный тип один — эти три как «основа» взаимоисключающи (надстройки поверх — можно).
- **Опциональные зависимости.** Зависимость может быть мягкой: `question-lifecycle` работает без `roles`. Сборщик подключает зависимость, только если она перечислена в `mechanics` пресета (или обязательна, как `claim-graph` для `question-lifecycle`).
- **Деактивация.** Опциональная механика, которой НЕТ в `mechanics` пресета, должна быть выкорчевана из base-скелета, если base несёт её заготовку. Сейчас это касается **roles** (base несёт скелет ролей) — см. ветку «ДЕАКТИВИРОВАТЬ» в `mechanics/roles/_about.md`.
- **Лифт, не форк.** Generic-улучшение, найденное при извлечении механики из класса, поднимается в base/механику, а не остаётся копией в классе (наследие ADR-0013).
