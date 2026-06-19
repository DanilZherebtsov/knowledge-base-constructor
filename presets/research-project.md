# Манифест пресета: research-project

version:       6            # строка-метка CLAUDE.md = «research-project · v6»
title-word:    "исследование"
central-type:  claims/ (добавлен вместо базового discovery/; набор типов wiki = claims/ · decisions/ · synthesis/ · principles/)
authority:     "Источники побеждают вики (S7) + S7b: каждое утверждение — с цитатой и локализацией (файл + страница / параграф / таймкод), lint-проверяемо"
work-layers:   [data/ (опц.), src/ (опц.)]
state-sections:[Стадия, Путь до цели, Сейчас в работе, Следующее (1–2 недели), Отвечено за последнюю неделю, Блокеры и риски, Открытые вопросы низкого приоритета]
domain-conv:   "Цитаты с локализацией: литература `(Smith 2024, p. 47)` / `(Smith 2024, §3.2)`; полевые `[interview-jones-2026-04-12.md, мин. 14:30]`; web — с фрагментом-якорем. Без локализации — `[требует уточнения локации]`"
interview:     # INTERVIEW-Q
  - "Главный исследовательский вопрос и аудитория результата?"
  - "Какие источники будут приходить (литература, полевые данные, переписка с экспертами)?"
  - "Будут ли собственные датасеты (data/) или код анализа (src/)?"
raw-defaults:  [literature/, fieldwork/, conversations/, decisions/, datasets/ (опц.)]
domain-lint:   "claim со статусом active/validated, чьи Counter-evidence заметно перевешивают Evidence (кандидат на ревизию) + цитаты без локализации (список мест `<страница>:<строка> — <фрагмент>`)"
close-op:      "question-closed | Q-NNN — со ссылками на извлечённые claim/synthesis"
mechanics:     [claim-graph, question-lifecycle]

---

## S1 — «О проекте» (для сборки)

Слово контекста — «исследование». Вместо одного абзаца «О проекте» — три размеченных поля (как у business S1 несёт свою структуру):
- **Главный исследовательский вопрос** — одно предложение: что именно выясняем.
- **Аудитория результата** — кто потребитель; **определяет жанр deliverable** (статья / отчёт / брифинг / датасет).
- **Рамки и ограничения** — методологические выборы, scope, временной горизонт, доступ к данным, этические рамки, дедлайн.

## S3 — формат центрального типа claims/ (для page-conventions)

Одна страница = одно утверждение + evidence chain + связи. Тело:

```markdown
# Claim: <одно предложение — само утверждение>

**Status:** active | open | validated | invalidated | superseded
**Scope:** где работает (область, период, контекст).
**Не работает:** граничные случаи.

## Формулировка
## Evidence (поддержка)        — каждый пункт с локализацией цитаты
## Counter-evidence (противоречащее) — + «Реакция claim'а»
## Связи                        — Supports / Contradicts / Refines
## История                      — датированные записи
```

Префиксы имён: `topic-`, `hypothesis-`, `finding-`, `definition-`.
Статусы claim'ов: `active / superseded` + эпистемические `open / validated / invalidated`.
Мягкий лимит claim — ≤ 100 строк (составной → разбить через `refines:`).

## Механики, реально используемые классом

- **claim-graph** — граф `supports / contradicts / refines` между claim'ами; обратный поиск по `sources:`, evidence-карта, инвентарь противоречий (особые формы query); claim-связи чинятся на lint.
- **question-lifecycle** — поток исследовательского вопроса: повестка в STATE → рабочий файл `output/q-NNN-<slug>.md` → извлечение канона в claims/synthesis/decisions/principles. Файл `methodology/question-lifecycle.md`.

## Механики, НЕ используемые (для контроля сборки)

- **roles** — отказ (ADR-0004); в methodology research НЕТ файла `roles.md`, в CLAUDE.md НЕТ раздела «Роли», в bootstrap НЕТ строки про роли. Конструктор НЕ должен тащить roles.md / раздел «Роли» / ROLES-FILL.
- **spec-lifecycle**, **decision-lifecycle** — не применяются (это saas/business).
