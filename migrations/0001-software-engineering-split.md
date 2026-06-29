---
id: 0001
title: "Раскол механики кода — software-engineering из codebase + spec-lifecycle"
adr: adr-0022
impact: структурная            # не file-swap; применяется ЭТОЙ инструкцией, не подменой файла
applies-to: saas-product       # любой собранный saas-инстанс на старой форме
requires:                      # входное состояние (читается из отпечатка сборки инстанса)
  spec-lifecycle: "<=3"        # цикл исполнения ещё ВНУТРИ spec-lifecycle
  software-engineering: absent
produces:                      # выходное состояние
  base: 21
  spec-lifecycle: 4
  software-engineering: 1
  saas-product: 2
---

# Миграция 0001 — раскол механики кода (`software-engineering`)

> **Исполняется агентом ВНУТРИ боевого репозитория saas-инстанса.** Подходит любому собранному `saas-product`, чей отпечаток несёт `spec-lifecycle@≤3` и не несёт `software-engineering` (цикл исполнения вшит в `spec-lifecycle`). Источники механики берутся из публичного зеркала `knowledge-base-constructor` (тот же канал, что профилактика использует для сверки версий). Подробности решения — ADR-0022.

## Что меняется и главный инвариант

Конструктор разделил две перепутанные оси: **единицу работы** (`spec-lifecycle`: спека/спринт/ADR-on-close) и **компетенцию кода** (`software-engineering`: цикл исполнения + владение + секреты + деплой). Цикл исполнения **переезжает** из `spec-lifecycle.md` в новую `software-engineering.md` и отвязывается от спеки.

> ⚠️ **Координированный переход — части ставятся ВМЕСТЕ.** Применить только `spec-lifecycle@4` (подменой файла) нельзя: цикл исчезнет из `spec-lifecycle.md`, а взамен не появится — инстанс потеряет дисциплину написания кода. Эта миграция ставит `software-engineering@1` + ужимает `spec-lifecycle@4` + перенацеливает `base@21` + поднимает `saas-product@2` за один проход.

**Инвариант миграции.** Меняется **только СТАНДАРТ-слой** — `methodology/*` + каркас-проза `CLAUDE.md` + отпечаток сборки. Всё остальное — байт-в-байт: `wiki/` (тела страниц, любые доп-типы), `raw/`, `STATE.md`, `output/`, `specs/`, `src/`, `data/`, `scripts/`, тесты, миграции БД. Проверяется diff'ом (Шаг 6).

## Шаг 1. Подготовка

1. **Бэкап.** Под git — чистое рабочее дерево или ветка `migrate-0001`.
2. **Взять апстрим** (зеркало) во временную папку и убедиться в версиях:
   ```bash
   git clone https://github.com/DanilZherebtsov/knowledge-base-constructor.git /tmp/constructor
   cat /tmp/constructor/versions.json   # base 21, spec-lifecycle 4, software-engineering 1, presets.saas-product 2
   ```
3. **Референс** (НЕ копировать целиком): `/tmp/constructor/mechanics/software-engineering/{software-engineering.md,_about.md}`, `/tmp/constructor/mechanics/spec-lifecycle/spec-lifecycle.md` (тело `@4`), `/tmp/constructor/base/{CLAUDE.md,methodology/ingest.md,methodology/bootstrap.md}`, `/tmp/constructor/presets/saas-product.md`.

## Шаг 2. Положить новую механику кода

Скопировать `software-engineering.md` → `methodology/software-engineering.md`.

**Роли — по гейту инстанса:** если в инстансе НЕ подключена механика `roles` (типичный saas — роли отложены, ADR-0004), образцы `_разработчик`/`_релиз-менеджер` НЕ копируем; файл `software-engineering.md` сам гейтит раздел ролей. Подключена `roles` И инстанс — деплоимый веб-продукт → скопировать оба образца в `roles/`.

Эта механика приносит инстансу **цикл исполнения** (тот же, что был в `spec-lifecycle@3`, — поведение не меняется, только переезжает) + правило «код побеждает вики» + дисциплину секретов и runtime-`data/`. Всё это де-факто уже действовало.

## Шаг 3. Ужать `spec-lifecycle` до `@4`

Заменить `methodology/spec-lifecycle.md` на апстрим-`@4`. В файле: раздел «Цикл исполнения задачи» исчезает (→ указатель на `software-engineering.md`); алгоритм спринта ссылается на цикл в `software-engineering.md`; чек-лист «Оценка новых знаний при завершении» становится **type-agnostic** («перебрать все типы, объявленные в проекте» — перечень из дерева «Архитектура» `CLAUDE.md`).

> **Это обезвреживает прививку доп-типов**, если инстанс её делал. Прежняя ручная правка «N типов» прямо в `spec-lifecycle.md` была хрупкой (апдейт затёр бы её). Теперь не нужна — `@4` сам берёт перечень из `CLAUDE.md`. **Проверь:** дерево «Архитектура» / раздел «Wiki: типы» в `CLAUDE.md` инстанса перечисляет все его типы (базовые + доменные расширения). Если да — синтез их покроет.

## Шаг 4. Перенацелить точки `base@21` в `CLAUDE.md` и `methodology/`

1. **Отпечаток (строка 3 `CLAUDE.md`):** `> **Сборка:** <class> · base@21 · spec-lifecycle@4 · software-engineering@1 · saas-product@2. …` (числа — из живого `versions.json`).
2. **Ловец «сделать сайт/бот/скрипт с нуля»** (раздел «Wiki: типы и операции») → форма base@21: «Подключена механика кода (`software-engineering`) — веди по ней…». (Заменить упоминания `codebase`/`spec-lifecycle` на `software-engineering`.)
3. **Указатель механики** в «Wiki: типы и операции» (always-on вход): добавить «**Владение кодом и цикл исполнения** → [methodology/software-engineering.md](methodology/software-engineering.md)».
4. **Always-on строка цикла** в «Принципы работы Claude → фаза действуй»: у saas-инстанса она уже есть (вставлял wiring `spec-lifecycle@3`). **Оставить**, обновив формулировку/ссылку под `software-engineering.md` (постановка → гейт субагентов → реализация → повторный гейт → доклад). Не удалять — иначе цикл потеряет always-on.
5. **Правило «Runtime-данные — в `data/`»** в «Дисциплине»: у saas-инстанса с `data/` уже есть (от `spec-lifecycle@3` §9). **Оставить** (теперь принадлежит `software-engineering`). Не дублировать.
6. **`methodology/ingest.md`, слот `OWNED-CODE`:** привести к base@21 и **заполнить** под механику: «раскладку код-папки, конвенции, цикл и роли ведёт [software-engineering.md](software-engineering.md)».
7. **`methodology/bootstrap.md`:** имя `software-engineering` вместо `codebase` (если упоминалось); `src/`/`data/` ленивые (у saas-инстанса уже так).

## Шаг 5. Гигиена

```bash
grep -rn '<<СЛОТ' .            # пусто (кроме литералов в base-прозе lint.md)
grep -rn 'codebase' .         # не должно быть ссылок на механику codebase
grep -rn 'Цикл исполнения' methodology/   # определение — только в software-engineering.md;
                                          # в spec-lifecycle.md лишь указатель
```

## Шаг 6. Артефакт-гейт (обязателен)

```bash
git status --short ; git diff --stat
```
Допустимы ТОЛЬКО: `CLAUDE.md`, `methodology/*.md` (правки) и **новый** `methodology/software-engineering.md`. **Любой** иной изменённый/удалённый путь — `src/`, `data/`, `specs/`, `scripts/`, тела `wiki/`, `STATE.md`, `output/`, `raw/` — **ошибка** → откатить (`git restore .`) и разобраться. Дополнительно: **цикл присутствует ровно в одном файле** (`software-engineering.md`); в `spec-lifecycle.md` — только указатель. Вердикт «ок» допустим, лишь если показан этот diff.

## Шаг 7. Проверка

1. **«Сделай профилактику»** — таблица сравнения частей с апстримом должна показать совпадение по всем: `base@21`, `spec-lifecycle@4`, `software-engineering@1`, `saas-product@2`. Доступных обновлений (этого перехода) больше нет.
2. **Поведение:** назови тестовую кодовую задачу — Claude должен развернуть цикл из `software-engineering.md` (постановка → проверка субагентами → реализация → тесты → доклад), как раньше. Оба режима (назвал фичу → спека+цикл; «вот спека, делай» → цикл) идентичны.

После успеха — ветку влить. **Эту инструкцию из боевого репо удалять не нужно** — она живёт в зеркале конструктора, не в инстансе.
