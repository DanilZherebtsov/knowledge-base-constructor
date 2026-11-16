#!/usr/bin/env python3
"""Shared helpers for the project's hooks — one copy for Claude Code, Codex and Qwen Code.

Every hook is called as `python3 hooks/<name>.py --program claude|codex|qwen` from that program's own
settings (.claude/settings.json, .codex/hooks.json, .qwen/settings.json). The project root is the folder
above hooks/ (where this file lives), not the event's cwd. A hook prints at most one JSON object on stdout
and nothing on stderr: Codex drops plain text that starts with "[", Qwen shows stderr to the human.
Errors go to tmp/knowledge-check/<program>/stderr.log. State and "alive" marks are kept per program, so a
run in one program never looks like the checks of another one working. Identical in the RU and EN editions.
"""
import datetime, json, os, re, sys

sys.dont_write_bytecode = True
HOOKS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HOOKS)
PROGRAMS = ("claude", "codex", "qwen")
_emitted = []


def program():
    """The program named by --program; anything else is an error in the hook's own command."""
    args = sys.argv[1:]
    name = args[args.index("--program") + 1] if "--program" in args and args.index("--program") + 1 < len(args) else ""
    if name not in PROGRAMS:
        log_error("unknown", "%s called without --program claude|codex|qwen" % os.path.basename(sys.argv[0]))
        sys.exit(0)
    return name


def locale():
    """ru or en: line 3 of AGENTS.md (`> **Версия:**` / `> **Version:**`), in a 2.x project mid-move — of CLAUDE.md;
    no such line — by the edition's folder of environment files (среда/ or environment/)."""
    for name in ("AGENTS.md", "CLAUDE.md"):
        try:
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                head = "".join(f.readline() for _ in range(6))
        except OSError:
            continue
        if re.search(r"\*\*Version\b", head):
            return "en"
        if re.search(r"\*\*Версия\b", head):
            return "ru"
    return "en" if os.path.isdir(os.path.join(ROOT, "methodology", "environment")) else "ru"


def ensure_tmp_ignored():
    """tmp/knowledge-check/ holds working state, including the human's last message: it ignores itself with its own
    .gitignore (`*`) before anything else is written there — whether or not the project is under git yet, a subfolder
    of someone else's repository or a worktree, and without touching the project's own .gitignore."""
    d = os.path.join(ROOT, "tmp", "knowledge-check")
    gi = os.path.join(d, ".gitignore")
    if os.path.isfile(gi):
        return
    try:
        os.makedirs(d, exist_ok=True)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# Working state of the project's checks (includes the human's last message). Never commit.\n*\n")
    except OSError:
        pass


def state_dir(prog):
    d = os.path.join(ROOT, "tmp", "knowledge-check", prog)
    ensure_tmp_ignored()
    os.makedirs(d, exist_ok=True)
    return d


def log_error(prog, msg):
    try:
        d = state_dir(prog)
        with open(os.path.join(d, "stderr.log"), "a", encoding="utf-8") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M ") + msg.strip()[:2000] + "\n")
    except Exception:
        pass


def mark_alive(prog, name):
    """tmp/knowledge-check/<program>/<name>-alive — the environment check reads these to see the hooks run."""
    try:
        with open(os.path.join(state_dir(prog), name + "-alive"), "w") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S\n"))
    except Exception:
        pass


def mark_time(prog, name):
    p = os.path.join(ROOT, "tmp", "knowledge-check", prog, name + "-alive")
    return os.path.getmtime(p) if os.path.isfile(p) else None


def read_event():
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        e = json.loads(raw) if raw.strip() else {}
        return e if isinstance(e, dict) else {}
    except Exception:
        return {}


def emit(obj):
    """The single JSON object of this run. A second call is a bug in the hook — ignored and logged."""
    if _emitted:
        return
    _emitted.append(1)
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def context(event_name, text):
    if text and text.strip():
        emit({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}})


def block(reason):
    emit({"decision": "block", "reason": reason})


def deny(reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def program_file(prog, loc=None):
    """Path of the program's environment file, as the agent should read it."""
    name = {"claude": "claude-code", "codex": "codex", "qwen": "qwen-code"}[prog]
    folder = "environment" if (loc or locale()) == "en" else "среда"
    return "methodology/%s/%s.md" % (folder, name)


def run(main):
    """Run a hook's main(); any failure is logged and lets the session go on (exit 0, no further output)."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as ex:
        args = sys.argv[1:]
        prog = args[args.index("--program") + 1] if "--program" in args and args.index("--program") + 1 < len(args) else ""
        log_error(prog if prog in PROGRAMS else "unknown", "%s: %r" % (os.path.basename(sys.argv[0]), ex))
    sys.exit(0)


def human_text(event):
    """The human's message from a UserPromptSubmit event, without the IDE context blocks VS Code prepends."""
    p = event.get("prompt")
    if not isinstance(p, str):
        return ""
    return re.sub(r"^(?:\s*<([a-z_]+)\b[^>]*>.*?</\1>)+", "", p, flags=re.S).strip()
