#!/usr/bin/env python3
"""Stop hook — reviewer of the finished reply (the «Request resolved» rule in CLAUDE.md).
A separate small model looks at the human's last message and the reply just sent and decides whether
the reply should be followed by a one-line offer to save knowledge to the project wiki. Cheap filters
run first; the model is called only when needed. Every call leaves a dated line in
tmp/knowledge-check/log.txt. A failure is told to the human once per chat; three failures in a row
switch the check off (.claude/knowledge-check.off)."""
import datetime, json, os, re, shutil, subprocess, sys, tempfile

MEM = re.compile(r"памят\w*\s+проекта|project memory|\bвик[иуеа]\b|\bwiki\b", re.I)
YES = re.compile(r"^\W*(да|давай|ок|окей|ok|okay|yes|запиши|записывай|сохрани|сохраняй)\b[^\n]{0,40}$", re.I)
NO = re.compile(r"^\W*(нет|не надо|не нужно|не записывай|не сохраняй|no|nope)\W*$", re.I)
CAP = 3


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if not p.lstrip().startswith("<"))
    return None


def is_offer(text):
    """An offer is a question near a mention of project memory / the wiki (the question may be its own sentence)."""
    text = text or ""
    return any("?" in text[m.start():m.start() + 300] for m in MEM.finditer(text))


def read_transcript(path):
    human, last_agent, pending, declined = "", "", False, False
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("isMeta") or e.get("isCompactSummary"):
            continue
        kind, content = e.get("type"), (e.get("message") or {}).get("content")
        if kind == "user":
            s = (text_of(content) or "").strip()
            if not s or s[0] in "<[" or s.startswith("Stop hook") or s.startswith("Caveat:"):
                continue
            if pending and NO.search(s):
                declined, pending = True, False
            elif pending and YES.search(s):
                pending = False
            human = s
        elif kind == "assistant" and isinstance(content, list):
            s = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
            if s:
                last_agent = s
                if is_offer(s):
                    pending = True
    return human, last_agent, pending, declined


def ensure_tmp_ignored(root):
    if not os.path.isdir(os.path.join(root, ".git")):
        return
    gi = os.path.join(root, ".gitignore")
    lines = open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []
    if not any(l.strip().strip("/") == "tmp" for l in lines):
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("\n" if lines and lines[-1].strip() else "") + "tmp/\n")


def emit(msg):
    sys.stdout.write(json.dumps({"decision": "block", "reason": msg}) + "\n")


def main():
    d = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if d.get("stop_hook_active"):
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or d.get("cwd") or "."
    off = os.path.join(root, ".claude", "knowledge-check.off")
    if os.path.exists(off):
        return
    kdir = os.path.join(root, "tmp", "knowledge-check")

    def log(v):
        try:
            ensure_tmp_ignored(root)
            os.makedirs(kdir, exist_ok=True)
            with open(os.path.join(kdir, "log.txt"), "a", encoding="utf-8") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M ") + v + "\n")
        except Exception:
            pass

    human, last_agent, pending, declined = "", "", False, False
    try:
        human, last_agent, pending, declined = read_transcript(d["transcript_path"])
    except Exception:
        pass
    reply = (d.get("last_assistant_message") or last_agent or "").strip()
    if not reply:
        return log("ERR no-reply")
    if declined:
        return log("SKIP declined")
    if pending or is_offer(reply):
        return log("SKIP offer")
    if reply.endswith("?"):
        return log("SKIP question")
    sid = str(d.get("session_id", ""))
    state_path = os.path.join(kdir, "state.json")
    try:
        state = json.load(open(state_path, encoding="utf-8"))
    except Exception:
        state = {}
    chats = state.setdefault("chats", {})
    chat = chats.setdefault(sid, {"blocks": 0, "told": False})
    if chat["blocks"] >= CAP:
        return log("SKIP cap")

    def save():
        try:
            os.makedirs(kdir, exist_ok=True)
            for k in list(chats)[:-50]:
                chats.pop(k, None)
            json.dump(state, open(state_path, "w", encoding="utf-8"))
        except Exception:
            pass

    ru = bool(re.search(r"[А-Яа-яЁё]", human + reply))
    q = ("You are an automatic check for a project that keeps a knowledge base (wiki). Below are the human's last "
         "message and the assistant reply just sent. Should the reply be followed by a one-line offer to save something "
         "to the knowledge base?\nAnswer BLOCK only if BOTH hold: (1) the exchange reached closure - the request is "
         "answered or solved, a conclusion was reached, or something new was discovered; (2) it produced knowledge the "
         "project will need again beyond this conversation: a decision the human made; a fact about the project or the "
         "outside world established here (price, deadline, contact, how an external program or service behaves, a data "
         "format); a confirmed non-obvious cause of a failure; a correction by the human about how things are done here.\n"
         "Answer OK if the info is one-off and unrelated to the project, it is only a wording or draft edit, it is a routine "
         "code change, it only restates what the project's own files or code already say (the file itself is the source), it is "
         "only the assistant's own recommendation or plan, or nothing new was learned. How an external program, service or "
         "system behaves (for example, the encoding or format another system exports) DOES count, even if it was found by "
         "reading a project file.\nAnswer with exactly one line: OK - or - "
         "BLOCK: <the knowledge in a few words, in " + ("Russian" if ru else "the human's language") + ">.\n\n"
         "HUMAN:\n" + human[-2000:] + "\n\nREPLY:\n" + reply[-4000:])
    verdict, err = "", ""
    exe = shutil.which("claude")
    if not exe:
        err = "no-claude"
    else:
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")}
        wd = os.path.join(tempfile.gettempdir(), "claude-knowledge-check")
        try:
            os.makedirs(wd, exist_ok=True)
            r = subprocess.run([exe, "-p", q, "--model", "haiku", "--no-session-persistence", "--setting-sources",
                                "project", "--strict-mcp-config", "--tools", ""], cwd=wd, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            lines = [re.sub(r"[*`_]", "", l).strip() for l in (r.stdout or "").splitlines()]
            first = next((l for l in lines if l.upper().startswith(("OK", "BLOCK"))), lines[0] if lines else "")
            if r.returncode != 0:
                err = "code %s" % r.returncode
            elif first.upper().startswith("BLOCK"):
                verdict = first
            else:
                verdict = "OK" if first.upper().startswith("OK") else "BAD"
        except Exception as ex:
            err = type(ex).__name__
    if err:
        log("ERR " + err)
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] >= 3:
            try:
                open(off, "w").close()
            except Exception:
                pass
            save()
            return emit("Проверка памяти проекта не смогла выполниться три раза подряд и отключилась. Скажи человеку одной "
                        "строкой: автоматических подсказок сохранить больше не будет — если что-то важно, пусть скажет "
                        "«запомни это»; включить обратно — «включи проверку памяти»." if ru else
                        "The project-memory check failed three times in a row and switched itself off. Tell the human in one "
                        "line: no more automatic offers to save - say 'remember this' when something matters; 'turn the "
                        "memory check back on' to re-enable.")
        if not chat["told"]:
            chat["told"] = True
            save()
            return emit("Проверка памяти проекта не смогла выполниться. Скажи человеку одной строкой: в этом чате я могу не "
                        "предложить сохранить важное — пусть скажет «запомни это». Больше об этом не пиши." if ru else
                        "The project-memory check could not run. Tell the human in one line that in this chat you may miss "
                        "offering to save something important - they can say 'remember this'. Do not mention it again.")
        return save()
    state["fails"] = 0
    if verdict.startswith("BLOCK") or verdict.upper().startswith("BLOCK"):
        what = verdict.split(":", 1)[1].strip() if ":" in verdict else ""
        chat["blocks"] += 1
        save()
        log("BLOCK")
        return emit(("Проверка памяти проекта: стоит сохранить «%s». Предложи это человеку одной строкой; записывать — только "
                     "после его «да». Уже предлагал или он отказался — ничего не добавляй." % what) if ru else
                    ("Project-memory check: worth saving '%s'. Offer it to the human in one line; write only after their yes. "
                     "Already offered or declined - add nothing." % what))
    save()
    log(verdict)


try:
    main()
except Exception:
    pass
