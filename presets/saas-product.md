# Манифест пресета: saas-product

version:       8            # версия манифеста пресета; в отпечатке сборки — saas-product@<versions.json>
title-word:    "продукт"
central-type:  architecture  (добавлен к базовым decisions/discovery/synthesis/principles)
authority:     "Код побеждает вики (классы с src/). S7b локализации цитат НЕТ — это research."
work-layers:   [specs/, src/, data/, scripts/]
state-sections:[Стадия, Путь до цели, Сейчас в работе, Следующее (1–2 недели), Завершено за последнюю неделю, Блокеры и риски, Известный техдолг]
domain-conv:   "Денежные суммы: рубли с пробелом-разделителем (220 000 ₽); другие валюты по своему соглашению ($1,200, €990). Спец-формата цитат нет."
interview:     # INTERVIEW-Q
  - "Что за продукт и для кого; текущая стадия?"
  - "Какие источники будут приходить (интервью/discovery, заметки решений, техдокументация)?"
  - "Уже есть код (src/) или runtime-данные (data/)?"
raw-defaults:  [discovery/, decisions/, technical/, (business/), (brainstorms/)]   # БЕЗ «прочее/» — это base/business catch-all, у saas его нет
domain-lint:   "Code drift центрального типа: страницы type: architecture без поля implementation: или с битыми путями в нём (код переименован/удалён — страница ссылается в пустоту)."
close-op:      none   # saas не пишет доменную операцию закрытия в wiki/log.md (явно «saas: нет» в CLOSE-OP)
mechanics:     [spec-lifecycle, software-engineering]

---

## Пояснения к слотам (для оркестратора)

- **S2 / S3.** Центральный тип — `architecture/`: модули, сервисы, контракты, инварианты, многошаговые workflow продукта + контракт runtime-данных. Обязательная секция `## Implementation` и YAML-поле `implementation:` (path-check, не семантический drift-детектор). Базовые `discovery/`/`synthesis/`/`principles/` сохранены; `decisions/` универсален.
- **S4.** К общему `output/` добавлены слои для класса с кодом: `specs/` (task-спеки, статус во frontmatter, плоско), `src/` (источник правды о поведении), `data/` (runtime-данные продукта: промпты, KB, конфиги, email-шаблоны — не в wiki), `scripts/` (временные/экспериментальные скрипты — **код-черновик**: не продукт-код `src/` и не документы-черновики `output/`; появляется по необходимости, не создаётся пустым при bootstrap).
- **S6 (lifecycle-файл).** `spec-lifecycle.md` — поток task-спек (бэклог в STATE → файл в specs/ active → извлечение ADR при acceptance → заморозка в completed) + спринты (≥3 спеки → SPRINT-<NAME>.md) + симметричное закрытие research-слоя (синтез-проход в ingest.md). Это и есть механика `spec-lifecycle`.
- **KNOWLEDGE-UNIT.** Дефолт — страница (saas не переопределяет на `claim`).
- **ROLES-FILL.** Заполняется примерами продуктовых ролей: devops, security-reviewer, support. Машинерия ролей — из base (всегда есть `roles/_шаблон.md`, `roles.md`, раздел «Роли»); конкретные роли не пред-заводятся, но `создай роль` доступно (ADR-0027, ревизия отложки из ADR-0004).

## Механики (обоснование выбора из {claim-graph, spec-lifecycle, software-engineering, question-lifecycle, decision-lifecycle}; роли — базовая, не механика)

- **spec-lifecycle** — ВКЛ (единица работы). `methodology/spec-lifecycle.md`, слой `specs/`, раздел «Поток task-спек» и «Спринты» в CLAUDE.md, S6-указатель.
- **software-engineering** — ВКЛ (компетенция кода). `methodology/software-engineering.md`, код-папка / `src/` / `data/`, always-on строка цикла исполнения в «фаза действуй», правило runtime-`data/`, слоты OWNED-CODE/S4/S7. Работает в паре со `spec-lifecycle`: спека — единица работы, эта механика — исполнение. Образцы ролей `_разработчик`/`_релиз-менеджер` предлагаются на спрос (роли теперь всегда в base — ADR-0027).
- **roles** — **базовая универсальная машинерия, не механика класса** (ADR-0027). `roles/_шаблон.md`, `roles.md`, раздел «Роли» присутствуют всегда (из base); saas не пред-заводит конкретные роли, но `создай роль` работает, и образцы продуктовых ролей из `software-engineering` предлагаются на спрос. Прежнее «roles ВЫКЛ» (ADR-0004) снято.
- **claim-graph** — ВЫКЛ. Центральный тип `architecture`, а не `claims`; KNOWLEDGE-UNIT = страница, не claim.
- **question-lifecycle** — ВЫКЛ. Нет `question-lifecycle.md`, CLOSE-OP = none (а не `question-closed`).
- **decision-lifecycle** — ВЫКЛ. Нет `decision-lifecycle.md`, CLOSE-OP = none (а не `decision-closed`); STATE без «Календаря обязательств».
