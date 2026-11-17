#!/usr/bin/env python3
"""Stop hook — reviewer of the finished reply (the «Request resolved» rule in AGENTS.md).
A separate model of the same program looks at the human's last message and the reply just sent and decides whether
the reply should be followed by a one-line offer to save knowledge to the project wiki. Cheap filters run first; the
model is called only when needed. The human's message and their answer to a pending offer come from consent.py
(tmp/knowledge-check/<program>/turn.json) — no program's session log is parsed. Every call leaves a dated line in
tmp/knowledge-check/<program>/log.txt. A failure is told to the human once per chat; three failures in a row switch the
check off for this program only (tmp/knowledge-check/<program>/off). The human's «выключи проверку памяти» is the
.knowledge-check.off file in the project root — for every program."""
import datetime, json, os, re, shutil, signal, subprocess, sys, tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

MEM = re.compile(r"памят\w*\s+проекта|project memory|\bвик[иуеа]\b|\bwiki\b", re.I)
CAP = 3
VERDICT = re.compile(r"^(?:[\w ]{0,20}:\s*)?(OK|BLOCK)\b", re.I)  # «OK», «BLOCK: …», also after a short label («Вердикт: BLOCK: …»)
SEPARATOR = re.compile(r"^[ \t*_>]*(Остальное\s*[—–-]+\s*решать сейчас не нужно|The rest\s*[—–-]+\s*no decision needed now)", re.M | re.I)
NO_REPLY = ("", "[no response text]")


def asks_human(reply):
    """The top part of a two-part reply (above the separator line; the whole reply if there is none) asks the human
    something: any "?" outside code blocks and quoted lines."""
    m = SEPARATOR.search(reply)
    top = reply[:m.start()] if m else reply
    top = re.sub(r"```.*?```|`[^`\n]*`", "", top, flags=re.S)
    return any("?" in line for line in top.splitlines() if not line.lstrip().startswith(">"))


def is_offer(text):
    """An offer is a question near a mention of project memory / the wiki."""
    text = text or ""
    return any("?" in text[m.start():m.start() + 300] for m in MEM.finditer(text))


SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR",
                "AI_AGENT", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
                "CODEX_TOOL_CALL_ID", "CODEX_CI", "QWEN_CODE", "QWEN_CODE_SESSION_ID", "QWEN_CODE_PROJECT_DIR",
                "QWEN_PROJECT_DIR")


def clean_env():
    """The nested run must not look like part of this session; how the program signs in and which provider it uses
    (CLAUDE_CODE_USE_BEDROCK, CLAUDE_CODE_OAUTH_TOKEN, CODEX_HOME, QWEN_HOME, …) stays as it is."""
    return {k: v for k, v in os.environ.items() if k not in SESSION_VARS}


def kill_tree(p):
    """Stop the model run with every process it started (wrappers start the model request in a child process)."""
    try:
        if os.name == "nt":
            tk = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
            subprocess.run([tk, "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def ask_model(prog, q):
    """(verdict line, error). Each program asks its own model, isolated: own temp folder, no project hooks."""
    wd = tempfile.mkdtemp(prefix="%s-knowledge-check-" % prog)  # private, empty, one per call: nothing to inherit or reuse
    try:
        return ask_in(prog, q, wd)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def ask_in(prog, q, wd):
    if prog == "claude":
        exe = os.environ.get("CLAUDE_CODE_EXECPATH")
        exe = exe if exe and os.path.isfile(exe) else common.which("claude")
        if not exe:
            return "", "no-claude"
        cmd = [exe, "-p", "--model", "haiku", "--no-session-persistence", "--setting-sources", "project",
               "--strict-mcp-config", "--tools", ""]
        out_file, timeout = None, 60
    elif prog == "codex":
        exe = common.which("codex")
        if not exe:
            return "", "no-codex"
        out_file = os.path.join(wd, "last.txt")
        cmd = [exe, "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "-s", "read-only",
               "-c", "model_reasoning_effort=\"low\"", "-o", out_file, "-"]
        timeout = 60
    else:
        exe = common.which("qwen")
        if not exe:
            return "", "no-qwen"
        cmd = [exe, "--safe-mode", "-p", "Answer as asked above.", "--output-format", "text", "--chat-recording", "false", "--max-session-turns", "1"]
        out_file, timeout = None, 80
    # The question goes on stdin, never as an argument: on Windows npm wrappers (.cmd) run through cmd.exe, where text
    # from the reply could turn into commands, lose its line breaks or outgrow the command line.
    p = subprocess.Popen(cmd, cwd=wd, env=clean_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", start_new_session=(os.name != "nt"))
    try:
        out, _ = p.communicate(input=q, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        p.communicate()
        raise
    r = subprocess.CompletedProcess(cmd, p.returncode, out, "")
    text = r.stdout or ""
    if out_file and os.path.exists(out_file):
        text = open(out_file, encoding="utf-8", errors="replace").read()
    if r.returncode != 0:
        return "", "code %s" % r.returncode
    lines = [re.sub(r"[*`_]", "", l).strip() for l in text.splitlines()]
    first = next((l for l in lines if VERDICT.match(l)), "")
    if not first:
        return "", "bad-verdict"  # an answer we cannot read is a failure, not an OK
    word = VERDICT.match(first)
    return (first[word.start(1):] if word.group(1).upper() == "BLOCK" else "OK"), ""


def main():
    prog = common.program()
    d = common.read_event()
    common.mark_alive(prog, "stop")
    if d.get("stop_hook_active"):
        return
    if os.path.exists(os.path.join(common.ROOT, ".knowledge-check.off")):
        return
    kdir = common.state_dir(prog)
    if os.path.exists(os.path.join(kdir, "off")):
        return

    def log(v):
        try:
            with open(os.path.join(kdir, "log.txt"), "a", encoding="utf-8") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M ") + v + "\n")
        except Exception:
            pass

    turn_path = os.path.join(kdir, "turn.json")
    try:
        turn = json.load(open(turn_path, encoding="utf-8"))
    except Exception:
        turn = {}
    sid = str(d.get("session_id", ""))
    if turn.get("session") != sid:
        turn = {"session": sid, "human": "", "offered": False, "declined": False}

    def save_turn():
        try:
            json.dump(turn, open(turn_path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass

    def block(reason):
        """Return the agent to the reply; the reason may come back as a prompt (Qwen) — consent.py skips it by `echo`."""
        turn["echo"] = reason
        save_turn()
        common.block(reason)

    human = turn.get("human", "")
    reply = d.get("last_assistant_message")
    reply = reply.strip() if isinstance(reply, str) else ""
    if reply in NO_REPLY:
        return log("ERR no-reply")
    if turn.get("declined"):
        return log("SKIP declined")
    if turn.get("offered") or is_offer(reply):
        turn["offered"] = True
        save_turn()
        return log("SKIP offer")
    if asks_human(reply):
        return log("SKIP question")
    if not human.startswith("/") and re.sub(r"^\W+|\W+$", "", human).casefold() in ("проще", "simpler"):
        return log("SKIP simpler")
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
         "HUMAN:\n" + human[-2000:] + "\n\nREPLY:\n" + (reply if len(reply) <= 4000 else reply[:2000] + "\n…\n" + reply[-2000:]))
    try:
        verdict, err = ask_model(prog, q)
    except Exception as ex:
        verdict, err = "", type(ex).__name__
    if err:
        log("ERR " + err)
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] >= 3:
            try:
                open(os.path.join(kdir, "off"), "w").close()
            except Exception:
                pass
            save()
            return block(
                "Проверка памяти проекта не смогла выполниться три раза подряд и отключилась в этой программе. Скажи человеку "
                "одной строкой: автоматических подсказок сохранить больше не будет — если что-то важно, пусть скажет "
                "«запомни это»; включить обратно — «включи проверку памяти»." if ru else
                "The project-memory check failed three times in a row and switched itself off in this program. Tell the human "
                "in one line: no more automatic offers to save - say 'remember this' when something matters; 'turn the "
                "memory check back on' to re-enable.")
        if not chat["told"]:
            chat["told"] = True
            save()
            return block(
                "Проверка памяти проекта не смогла выполниться. Скажи человеку одной строкой: в этом чате я могу не "
                "предложить сохранить важное — пусть скажет «запомни это». Больше об этом не пиши." if ru else
                "The project-memory check could not run. Tell the human in one line that in this chat you may miss "
                "offering to save something important - they can say 'remember this'. Do not mention it again.")
        return save()
    state["fails"] = 0
    if verdict.upper().startswith("BLOCK"):
        what = verdict.split(":", 1)[1].strip() if ":" in verdict else ""
        chat["blocks"] += 1
        save()
        turn["offered"] = True
        save_turn()
        log("BLOCK")
        return block(
            ("Проверка памяти проекта: стоит сохранить «%s». Предложи это человеку одной строкой; записывать — только "
             "после его «да». Уже предлагал или он отказался — ничего не добавляй." % what) if ru else
            ("Project-memory check: worth saving '%s'. Offer it to the human in one line; write only after their yes. "
             "Already offered or declined - add nothing." % what))
    save()
    log(verdict)


if __name__ == "__main__":
    common.run(main)
