#!/usr/bin/env python3
"""Environment check — without the model. One script for Claude Code, Codex and Qwen Code.

Run by the agent as its first action when the context has no «Program: …» line from hooks/start.py
(`python3 hooks/env_check.py`), and by start.py itself at every session start (`--program <name>`).
It names the program (Codex, Qwen, then Claude — Claude's variables are inherited by programs launched from it)
and checks that program's setup: what loads the rules, whether the project's checks are allowed to run,
the rules-file limit, built-in memory, the command the answer check needs.
Output: lines for the agent — «ВСЁ В ПОРЯДКЕ» / "ALL GOOD", or «СКАЖИ ЧЕЛОВЕКУ: …» / "TELL THE HUMAN: …",
one per problem; the language follows line 3 of AGENTS.md. Changes nothing but its own "told once" marks in
tmp/knowledge-check/; exit code 0 always.
"""
import json, os, re, shutil, subprocess, sys, time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

CODEX_DEFAULT_LIMIT = 32768
ALIVE = 15 * 60          # a hook ran within this many seconds — the checks are on
NAMES = {"claude": "Claude Code", "codex": "Codex", "qwen": "Qwen Code"}

M = {
    "no_program": ("не смог определить программу, в которой открыт проект: правила проекта действуют, но его автоматические "
                   "проверки здесь не работают. Проект рассчитан на Claude Code, Codex и Qwen Code.",
                   "could not tell which program the project is open in: the project's rules apply, but its automatic checks "
                   "do not work here. The project is made for Claude Code, Codex and Qwen Code."),
    "claude_md": ("правила проекта в Claude Code подключает файл `CLAUDE.md` из одной строки `@AGENTS.md`; его нет или он "
                  "изменён — без него Claude может не загрузить правила. Верни файл с этой строкой; сами правила — в `AGENTS.md`.",
                  "in Claude Code the project's rules are connected by `CLAUDE.md`, a single line `@AGENTS.md`; it is missing or "
                  "changed — without it Claude may not load the rules. Put the file back with that line; the rules live in `AGENTS.md`."),
    "claude_desktop": ("проект открыт в Claude Desktop: там автоматические проверки проекта не работают — правила держатся текстом. "
                       "Особенности — в файле среды Claude Code, раздел о Claude Desktop.",
                       "the project is open in Claude Desktop: the project's automatic checks do not run there — the rules hold as "
                       "text. See the Claude Code environment file, the Claude Desktop section."),
    "claude_hooks": ("автоматические проверки проекта не запускаются в Claude Code. Обычно их выключила настройка "
                     "`disableAllHooks` в настройках Claude Code или не найден Python 3 (ни `python3`, ни `python`, ни `py`). Без них я могу не заметить устаревший план и "
                     "забыть предложить сохранить важное.",
                     "the project's automatic checks are not running in Claude Code. Usually the `disableAllHooks` setting turned "
                     "them off, or Python 3 is not found (neither `python3`, nor `python`, nor `py`). Without them I may miss an outdated plan and forget to offer saving."),
    "codex_limit": ("правила проекта доходят до меня не полностью: Codex читает только первые %d байт из %d. Работать по ним "
                    "сейчас нельзя — не берись за задачу, пока не исправлено. Исправляется один раз: доверить папку (тогда лимит "
                    "поднимает настройка самого проекта) или строкой `project_doc_max_bytes = 262144` в начале файла настроек "
                    "Codex `%s` (до первой строки в квадратных скобках) и перезапуском Codex.",
                    "the project's rules reach me only in part: Codex reads only the first %d of %d bytes. Do not take on a task "
                    "until this is fixed. One-time fix: trust the folder (then the project's own setting raises the limit) or put "
                    "`project_doc_max_bytes = 262144` at the top of Codex's settings file `%s` (before the first line in square "
                    "brackets) and restart Codex."),
    "codex_override": ("рядом с `AGENTS.md` лежит `AGENTS.override.md` — Codex читает его вместо правил проекта. Если это не "
                       "нарочно — переименуй или удали его.",
                       "`AGENTS.override.md` sits next to `AGENTS.md` — Codex reads it instead of the project's rules. If that "
                       "is not on purpose, rename or remove it."),
    "codex_hooks": ("в этом проекте выключены автоматические проверки — без них я могу не заметить устаревший план работ или "
                    "забыть предложить сохранить важное. Включить один раз: %s. Не хочешь включать — скажи, я запишу это и больше "
                    "не буду напоминать.",
                    "the project's automatic checks are off — without them I may miss an outdated plan or forget to offer saving. "
                    "Turn them on once: %s. If you do not want them, say so — I will note it and stop reminding."),
    "codex_how_trusted": ("набрать в Codex `/hooks` и одобрить проверки этого проекта, затем перезапустить Codex",
                          "type `/hooks` in Codex and approve this project's checks, then restart Codex"),
    "codex_how_untrusted": ("сначала доверить эту папку (Codex спрашивает при открытии; нажать «Trust and continue», не «Open "
                            "restricted»), затем набрать `/hooks` и одобрить проверки этого проекта, затем перезапустить Codex",
                            "first trust this folder (Codex asks when it opens; choose «Trust and continue», not «Open restricted»), "
                            "then type `/hooks` and approve this project's checks, then restart Codex"),
    "codex_restart": ("если настройки Codex меняли недавно — перезапусти Codex: до перезапуска он читает старые настройки.",
                      "if Codex's settings were changed recently, restart Codex: until then it reads the old settings."),
    "codex_memory": ("встроенная память Codex включена: то, что она запоминает, идёт мимо памяти проекта (папки `wiki/`) и в других "
                     "программах не видно. Важное прошу записывать в память проекта — скажи «запомни это».",
                     "Codex's built-in memory is on: what it remembers bypasses the project's memory (the `wiki/` folder) and is "
                     "not seen in other programs. Ask me to save what matters to project memory — say 'remember this'."),
    "qwen_rejected": ("проверки этого проекта в Qwen Code отклонены и больше не предлагаются. Включить: набрать `/hooks` в Qwen "
                      "Code и одобрить проверки этого проекта.",
                      "this project's checks were rejected in Qwen Code and are not offered again. To turn them on: type `/hooks` "
                      "in Qwen Code and approve this project's checks."),
    "qwen_pending": ("проверки этого проекта в Qwen Code не одобрены (или изменились — так бывает после обновления проекта и после "
                     "правки файла `.env`). Одобрить один раз: открыть Qwen Code в папке проекта в окне и согласиться, когда он "
                     "спросит о проверках проекта, или набрать `/hooks`.",
                     "this project's checks are not approved in Qwen Code (or they changed — this happens after a project update "
                     "and after editing the `.env` file). Approve once: open Qwen Code in the project folder interactively and "
                     "agree when it asks about the project's checks, or type `/hooks`."),
    "qwen_folder": ("Qwen Code запущен не в папке проекта — проверки проекта он берёт только из папки запуска. Открой Qwen Code "
                    "в папке проекта.",
                    "Qwen Code was started outside the project folder — it takes the project's checks only from the folder it "
                    "starts in. Open Qwen Code in the project folder."),
    "qwen_context": ("в личных настройках Qwen Code задан свой список файлов правил без `AGENTS.md` — правила проекта не "
                     "загружаются. Добавь `AGENTS.md` в настройку `context.fileName`.",
                     "Qwen Code's personal settings name their own rules files without `AGENTS.md` — the project's rules are not "
                     "loaded. Add `AGENTS.md` to the `context.fileName` setting."),
    "qwen_memory": ("встроенная авто-память Qwen Code включена: после каждого моего ответа она делает отдельный запрос к модели "
                    "и может записать заметку мимо памяти проекта (папки `wiki/`). Выключить — `\"memory\": {\"enableManagedAutoMemory\": "
                    "false}` в настройках Qwen Code; важное прошу записывать в память проекта — скажи «запомни это». Больше об этом "
                    "не напомню.",
                    "Qwen Code's built-in auto-memory is on: after each reply of mine it makes a separate request to the model and may "
                    "write a note that bypasses the project memory (the `wiki/` folder). To turn it off — `\"memory\": "
                    "{\"enableManagedAutoMemory\": false}` in Qwen Code's settings; please ask me to save what matters to project "
                    "memory — say 'remember this'. I won't mention this again."),
    "guard": ("автоматическая проверка, не дающая мне править присланные документы в папке `raw/`, не работает — похоже, она "
              "не одобрена. Правки этих документов я не делаю и без неё.",
              "the automatic check that stops me from editing received documents in `raw/` is not working — it looks "
              "unapproved. I do not edit those documents without it either."),
    "judge_cmd": ("проверка моих ответов не сможет работать: нет команды `%s` там, где её ищут проверки проекта. Поставить её "
                  "или выключить проверку словами «выключи проверку памяти».",
                  "the answer check cannot work: the `%s` command is not where the project's checks look for it. Install it "
                  "or switch the check off by saying 'turn the memory check off'."),
    "judge_dead": ("проверка моих ответов (подсказка сохранить важное), похоже, не работает: остальные проверки проекта "
                   "срабатывают, а она — нет. Обычно её одобрение снято после обновления проверок: одобрить все проверки "
                   "проекта заново.",
                   "the answer check (the offer to save what matters) seems not to work: the project's other checks run, it does "
                   "not. Usually its approval was dropped after the checks were updated: approve all of the project's checks again."),
    "migration": ("переход проекта на версию 3.0.0 не закончен — продолжи профилактику (скажи «сделай профилактику»).",
                  "the project's move to 3.0.0 is not finished — continue the maintenance (say 'run maintenance')."),
}


def t(key, loc, *args):
    s = M[key][1 if loc == "en" else 0]
    return s % args if args else s


def read(path):
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""


def detect():
    """Codex, Qwen, then Claude: Claude's variables are inherited by programs launched from a Claude session."""
    e = os.environ
    if e.get("CODEX_THREAD_ID") or e.get("CODEX_SESSION_ID"):
        return "codex"
    if e.get("QWEN_CODE") or e.get("QWEN_CODE_SESSION_ID"):
        return "qwen"
    if e.get("CLAUDECODE") or e.get("AI_AGENT", "").startswith("claude-code"):
        return "claude"
    return None


def alive(prog, name, within=ALIVE):
    p = os.path.join(common.ROOT, "tmp", "knowledge-check", prog, name + "-alive")
    return os.path.isfile(p) and time.time() - os.path.getmtime(p) < within


def log_has(*needles):
    log = read(os.path.join(common.ROOT, "wiki", "log.md"))
    return any(n in log for n in needles)


def hooks_declined():
    return log_has("настройка | хуки: не ставим", "setup | hooks: not adding")


def guard_declined():
    return log_has("настройка | страж raw/: не ставим", "setup | raw/ guard: not adding")


def migration_unfinished():
    claude_md = read(os.path.join(common.ROOT, "CLAUDE.md"))
    return "**Версия:**" in claude_md[:400] or "**Version:**" in claude_md[:400] or \
        os.path.isfile(os.path.join(common.ROOT, "tmp", "0007", "hod.md"))


def top_level_int(toml, key):
    try:
        import tomllib
        v = tomllib.loads(toml).get(key)
        return int(v) if isinstance(v, int) else None
    except ImportError:
        head = re.split(r"(?m)^\s*\[", toml, maxsplit=1)[0]
        m = re.search(r"(?m)^\s*%s\s*=\s*(\d+)" % re.escape(key), head)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def git_top(path):
    try:
        git = common.which("git")
        if not git:
            return None
        top = subprocess.run([git, "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return os.path.realpath(top) if top else None
    except Exception:
        return None


def codex_trusted(toml, root):
    """Codex trusts by the exact project path (the git root when the folder is in one). On Windows the key is often a
    literal string — [projects.'C:\\…'] — and the case of the path does not matter."""
    target = os.path.normcase(git_top(root) or os.path.realpath(root))
    items = []
    try:
        import tomllib  # Python 3.11+
        items = [(k, v.get("trust_level")) for k, v in tomllib.loads(toml).get("projects", {}).items() if isinstance(v, dict)]
    except Exception:
        for m in re.finditer(r"""(?ms)^\s*\[projects\.(?:"((?:[^"\\]|\\.)*)"|'([^']*)')\]\s*$(.*?)(?=^\s*\[|\Z)""", toml):
            key = m.group(2) if m.group(2) is not None else re.sub(r"\\(.)", r"\1", m.group(1))
            level = re.search(r'trust_level\s*=\s*"([^"]*)"', m.group(3))
            items.append((key, level.group(1) if level else None))
    return any(level == "trusted" and os.path.normcase(os.path.realpath(k)) == target for k, level in items)


def proc_info(pid):
    """(start epoch, name, parent pid). Mac — sysctl (Codex's sandbox forbids `ps`); elsewhere — `ps`."""
    try:
        if sys.platform == "darwin":
            import ctypes, ctypes.util, struct
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
            size = ctypes.c_size_t(0)
            if libc.sysctl(mib, 4, None, ctypes.byref(size), None, 0) or not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) or size.value < 564:
                return None
            sec, usec = struct.unpack_from("qi", buf.raw, 0)  # kinfo_proc: p_starttime at 0, p_comm at 243, e_ppid at 560
            return sec + usec / 1e6, buf.raw[243:260].split(b"\0")[0].decode(errors="replace"), struct.unpack_from("i", buf.raw, 560)[0]
        out = subprocess.run(["ps", "-o", "ppid=,etime=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        if not out:
            return None
        ppid, etime, comm = out.split(None, 2)
        secs = sum(u * v for u, v in zip((1, 60, 3600, 86400), reversed([int(x) for x in re.split(r"[-:]", etime)])))
        return time.time() - secs, comm, int(ppid)
    except Exception:
        return None


def started_before(name, mtime):
    """The program process (by name) started before mtime? None if unknown."""
    pid = os.getppid()
    for _ in range(8):
        info = proc_info(pid)
        if not info:
            return None
        start, comm, pid = info
        if name in os.path.basename(comm).lower():
            return start < mtime
    return None


def check_claude(loc, from_hook):
    out = []
    lines = [l.strip() for l in read(os.path.join(common.ROOT, "CLAUDE.md")).splitlines() if l.strip()]
    if "@AGENTS.md" not in lines and not migration_unfinished():  # the import line; the human's own lines next to it are fine
        out.append(t("claude_md", loc))
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("claude-desktop") and not from_hook:  # a hook ran: checks work here
        if not told("claude", "desktop-told"):  # once per project, not in every chat
            out.append(t("claude_desktop", loc))
            common.mark_alive("claude", "desktop-told")
    elif not alive("claude", "start", 24 * 3600) and not alive("claude", "consent") and not hooks_declined():
        out.append(t("claude_hooks", loc))
    else:
        out += check_judge("claude", loc, True)
    return out


def check_codex(loc, from_hook):
    out = []
    root = common.ROOT
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    user_cfg = read(os.path.join(home, "config.toml"))
    proj_cfg = read(os.path.join(root, ".codex", "config.toml"))
    trusted = codex_trusted(user_cfg, root)
    limit = top_level_int(user_cfg, "project_doc_max_bytes") or CODEX_DEFAULT_LIMIT
    if trusted and top_level_int(proj_cfg, "project_doc_max_bytes"):
        limit = top_level_int(proj_cfg, "project_doc_max_bytes")
    top = git_top(root) or os.path.realpath(root)
    need, d = 0, os.path.realpath(root)
    while True:  # the budget: every AGENTS.md from the git root down to the project
        if os.path.isfile(os.path.join(d, "AGENTS.override.md")):
            need += os.path.getsize(os.path.join(d, "AGENTS.override.md"))
        elif os.path.isfile(os.path.join(d, "AGENTS.md")):
            need += os.path.getsize(os.path.join(d, "AGENTS.md"))
        if d == top or os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    if need > limit:
        out.append(t("codex_limit", loc, limit, need, os.path.join(home, "config.toml")))
    if os.path.isfile(os.path.join(root, "AGENTS.override.md")):
        out.append(t("codex_override", loc))
    hooks_on = from_hook or any(alive("codex", n) for n in ("consent", "start", "guard", "stop"))
    if not hooks_on and not hooks_declined():
        out.append(t("codex_hooks", loc, t("codex_how_trusted" if trusted else "codex_how_untrusted", loc)))
    feat = re.split(r"(?m)^\s*\[features\]\s*$", user_cfg, maxsplit=1)
    if len(feat) == 2 and re.search(r"(?m)^\s*memories\s*=\s*true", re.split(r"(?m)^\s*\[", feat[1], maxsplit=1)[0]):
        out.append(t("codex_memory", loc))
    try:
        cfg_mtime = os.path.getmtime(os.path.join(home, "config.toml"))
        if time.time() - cfg_mtime < 86400 and started_before("codex", cfg_mtime):
            out.append(t("codex_restart", loc))
    except OSError:
        pass
    out += check_judge("codex", loc, hooks_on)
    return out


def qwen_home():
    return os.environ.get("QWEN_HOME") or os.path.join(os.path.expanduser("~"), ".qwen")


def check_qwen(loc, from_hook):
    out = []
    root = os.path.realpath(common.ROOT)
    home = qwen_home()
    proj_dir = os.environ.get("QWEN_CODE_PROJECT_DIR")
    if proj_dir and os.path.realpath(proj_dir) != root:
        out.append(t("qwen_folder", loc))
    try:
        user = json.loads(read(os.path.join(home, "settings.json")) or "{}")
    except Exception:
        user = {}
    names = ((user.get("context") or {}).get("fileName")) if isinstance(user.get("context"), dict) else user.get("contextFileName")
    if names and "AGENTS.md" not in (names if isinstance(names, list) else [names]):
        out.append(t("qwen_context", loc))
    try:
        proj = json.loads(read(os.path.join(common.ROOT, ".qwen", "settings.json")) or "{}")
    except Exception:
        proj = {}
    mem = [((s.get("memory") or {}) if isinstance(s.get("memory"), dict) else {}).get("enableManagedAutoMemory") for s in (proj, user)]
    memory_on = next((m for m in mem if isinstance(m, bool)), True)  # project over personal; Qwen's default is on
    if memory_on and not told("qwen", "memory-told"):
        out.append(t("qwen_memory", loc))
        common.mark_alive("qwen", "memory-told")  # told once per project
    hooks_on = from_hook or any(alive("qwen", n) for n in ("consent", "start", "guard", "stop"))
    if not hooks_on and not hooks_declined():
        approvals_path = os.environ.get("QWEN_CODE_HOOK_APPROVALS_PATH") or os.path.join(home, "hookApprovals.json")
        try:
            status = (json.loads(read(approvals_path) or "{}").get(root) or {}).get("status")
        except Exception:
            status = None
        out.append(t("qwen_rejected" if status == "rejected" else "qwen_pending", loc))
    out += check_judge("qwen", loc, hooks_on)
    return out


def check_judge(prog, loc, hooks_on):
    if os.path.exists(os.path.join(common.ROOT, ".knowledge-check.off")) or \
            os.path.exists(os.path.join(common.ROOT, "tmp", "knowledge-check", prog, "off")):
        return []
    out = []
    cmd = {"claude": "claude", "codex": "codex", "qwen": "qwen"}[prog]
    if prog != "claude" and not common.which(cmd):
        out.append(t("judge_cmd", loc, cmd))
    consent, stop, first = common.mark_time(prog, "consent"), common.mark_time(prog, "stop"), common.mark_time(prog, "consent-first")
    since = stop if stop is not None else first  # the last finished reply, or the first turn if none has finished
    if prog != "claude" and hooks_on and consent and since is not None and consent - since > 86400:  # approvals: Codex, Qwen
        out.append(t("judge_dead", loc))  # turns go on for a day, the after-reply check has not run
    return out


def check_guard(prog, loc, from_hook):
    """Run by the agent, this check is itself a shell call: an approved raw/ guard has just left its mark."""
    if from_hook or not os.path.isdir(os.path.join(common.ROOT, "raw")) or guard_declined():
        return []
    if not (alive(prog, "consent") or alive(prog, "start")):
        return []  # the checks are off altogether — said elsewhere
    return [] if alive(prog, "guard", 120) else [t("guard", loc)]


AGAIN = []  # --again (maintenance): repeat the notices that are otherwise told once per project


def told(prog, mark):
    return not AGAIN and common.mark_time(prog, mark) is not None


def report(prog, loc, from_hook=False):
    """Problems for the human, as lines without the prefix."""
    return report_all(prog, loc, from_hook)


def report_all(prog, loc, from_hook=False):
    out = []
    if migration_unfinished() and os.path.isfile(os.path.join(common.ROOT, "hooks", "start.py")):
        out.append(t("migration", loc))
    if prog == "claude":
        out += check_claude(loc, from_hook)
    elif prog == "codex":
        out += check_codex(loc, from_hook)
    elif prog == "qwen":
        out += check_qwen(loc, from_hook)
    if prog in common.PROGRAMS:
        out += check_guard(prog, loc, from_hook)
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console's code page can't print Cyrillic
    loc = common.locale()
    args = sys.argv[1:]
    if "--again" in args:
        AGAIN.append(1)
    prog = args[args.index("--program") + 1] if "--program" in args and args.index("--program") + 1 < len(args) else detect()
    tell = "TELL THE HUMAN: " if loc == "en" else "СКАЖИ ЧЕЛОВЕКУ: "
    if prog not in common.PROGRAMS:
        print(tell + t("no_program", loc))
        return
    print(("Program: %s. Its specifics: %s" if loc == "en" else "Программа: %s. Особенности — %s")
          % (NAMES[prog], common.program_file(prog, loc)))
    problems = report(prog, loc)
    if not problems:
        print("ALL GOOD" if loc == "en" else "ВСЁ В ПОРЯДКЕ")
    for p in problems:
        print(tell + p)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print(("TELL THE HUMAN: could not check the environment (%s)." if common.locale() == "en"
               else "СКАЖИ ЧЕЛОВЕКУ: не смог проверить настройку среды (%s).") % type(ex).__name__)
    sys.exit(0)
