#!/usr/bin/env python3
"""UserPromptSubmit hook: service lines before every agent turn, at the end of the context, where they are
actually followed (consent and the reminder limit leak when they live only in AGENTS.md). Second line only when
the human's whole message is «проще» / "simpler": rewrite the last reply simpler.

Also keeps, for the answer check (judge.py), the human's last message and their answer to a pending offer to
save (yes / no) — one small file per program in tmp/knowledge-check/<program>/, overwritten every turn.
Prompts of subagents (an event with agent_id) are not the human's and are skipped, and so are a program's own
continuations: Qwen Code sends this event after every tool result with an empty prompt, and after a Stop-hook block
with the block's reason as the prompt (judge.py keeps that reason as `echo` in turn.json)."""
import json, os, re, sys, unicodedata

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

YES = re.compile(r"^\W*(да|давай|ок|окей|ok|okay|yes|yeah|yep|sure|go ahead|please do|запиши|записывай|сохрани|сохраняй|save|record)\b[^\n]{0,40}$", re.I)
NO = re.compile(r"^\W*(нет,? не надо|нет|не надо|не нужно|не записывай|не сохраняй|no|nope|not needed|skip it|don['’]?t save(?: it)?|don['’]?t record(?: it)?)(?:\W+(?:спасибо|thanks|thank you))?\W*$", re.I)

LINES = (
    "[consent check] Write to the wiki only after the human explicitly says yes to your offer to save; "
    "an on-topic reply is not a yes. Project memory is the wiki/ folder via ingest, not the program's built-in memory. "
    "An unanswered offer is not a refusal, but do not repeat it every turn: at most one reminder per chat, in the reply "
    "that wraps up, with no reproach; you may extend a pending offer only when new facts appear. Routine code changes you "
    "just made are not worth offering.\n"
    "[one answer, one question] A yes answers only a question you actually asked and never carries over to one you did not "
    "ask; if several of your questions are open and the reply does not say which, say how you took it, and do nothing "
    "irreversible, no wiki write and no overwrite until the human answers that one."
)


def plain_line(selected):
    return ("[plain] The human's whole message is «проще»/\"simpler\": rewrite your last substantive reply (not a service line) "
            "simpler and shorter — same facts, decisions and questions; you may explain a word or give an example, but add no new "
            "facts or decisions. If that reply offered «проще»/\"simpler\" as an option, it is the answer. If it was a pre-work "
            "understanding check, a plan or a shown result (mockup, document, draft for others)"
            + (", or the human selected text in the editor" if selected else "")
            + ", ask in one phrase what to simplify: the message"
            + (", the thing itself or the selection." if selected else " or the thing itself."))


def is_simpler(text):
    if text.startswith("/"):
        return False
    i, j = 0, len(text)
    while i < j and unicodedata.category(text[i])[0] not in "LN":
        i += 1
    while j > i and unicodedata.category(text[j - 1])[0] not in "LN":
        j -= 1
    return text[i:j].casefold() in ("проще", "simpler")


def is_echo(prog, human):
    """The prompt is judge.py's own block reason fed back by the program (Qwen Code)."""
    try:
        turn = json.load(open(os.path.join(common.state_dir(prog), "turn.json"), encoding="utf-8"))
    except Exception:
        return False
    return bool(turn.get("echo")) and human == turn["echo"].strip()


def remember(prog, event, human):
    """Last human message and the answer to a pending offer, for judge.py."""
    d = common.state_dir(prog)
    path = os.path.join(d, "turn.json")
    try:
        prev = json.load(open(path, encoding="utf-8"))
    except Exception:
        prev = {}
    sid = str(event.get("session_id", ""))
    same = prev.get("session") == sid
    offered = same and prev.get("offered", False)
    declined = same and prev.get("declined", False)
    if offered and NO.search(human):
        declined = True
    offered = False  # an offer is answered or left unanswered by the next message; either way it no longer silences
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"session": sid, "human": human[-2000:], "offered": offered, "declined": declined, "echo": ""}, f, ensure_ascii=False)


def main():
    prog = common.program()
    event = common.read_event()
    common.mark_alive(prog, "consent")
    if common.mark_time(prog, "consent-first") is None:
        common.mark_alive(prog, "consent-first")  # env_check: Stop never ran since the first turn → the answer check is off
    if event.get("agent_id"):
        return  # a subagent's prompt, not the human's
    raw = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
    human = common.human_text(event)
    if not human or is_echo(prog, human):
        return  # the program continues on its own (after a tool result or our Stop-hook block): not the human
    text = LINES
    if is_simpler(human):
        text += "\n" + plain_line("<ide_selection" in raw)
    try:
        remember(prog, event, human)
    except Exception as ex:
        common.log_error(prog, "consent.remember: %r" % ex)
    common.context("UserPromptSubmit", text)


if __name__ == "__main__":
    common.run(main)
