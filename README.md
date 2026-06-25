# knowledge-base-constructor

**Идея — LLM-вики.** Источники проекта однократно компилируются в постоянную связанную вику; дальше Claude отвечает, читая её, а не выводя одно и то же из сырых документов заново при каждом вопросе.

Единый источник, из которого собираются такие LLM-вики-проекты под разные задачи (saas / research / business) и любые их комбинации механик (роли, claim-граф, lifecycle). Один источник вместо копий-шаблонов — версионируется по частям, без дрейфа.

- **Где лежит:** публичное зеркало — [github.com/DanilZherebtsov/knowledge-base-constructor](https://github.com/DanilZherebtsov/knowledge-base-constructor).
- **Запустить:** склонируй [публичное зеркало](https://github.com/DanilZherebtsov/knowledge-base-constructor) в пустой репозиторий и в Claude Code просто начни разговор — конструктор интервью соберёт проект (на выходе — заполненный `CLAUDE.md` и структура вики; леса конструктора удаляются сами). См. [START-HERE.md](START-HERE.md).
- **Состав:** `base/` (скелет + универсальная methodology) · `mechanics/` (отстёгиваемые механики) · `presets/` (манифесты классов) · [`versions.json`](versions.json) (версии частей).
- **Версии и обновления:** каждая часть версионируется отдельно. Собранный проект несёт **отпечаток** использованных версий (строка 3 его `CLAUDE.md`) и на lint сверяет их с `versions.json` — слышит только про те части, которые реально использует. Механика — [ASSEMBLY.md](ASSEMBLY.md) и [base/methodology/lint.md](base/methodology/lint.md).
