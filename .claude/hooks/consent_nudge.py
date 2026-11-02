#!/usr/bin/env python3
"""UserPromptSubmit hook: service lines before every agent turn, at the end of the context, where they are
actually followed. Tested: consent and the reminder limit leak when they live only in CLAUDE.md.
Second line only when the human's whole message is «проще» / "simpler": rewrite the last reply simpler."""
import json, re, sys, unicodedata
raw = b""
try:
    raw = sys.stdin.buffer.read()
except Exception:
    pass


def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


out("[consent check] Write to the wiki only after the human explicitly says yes to your offer to save; "
    "an on-topic reply is not a yes. Project memory is the wiki/ folder via ingest, not Claude Code's built-in memory. "
    "An unanswered offer is not a refusal, but do not repeat it every turn: at most one reminder per chat, in the reply "
    "that wraps up, with no reproach; you may extend a pending offer only when new facts appear. Routine code changes you just made are not worth offering.")
try:
    p = json.loads(raw.decode("utf-8")).get("prompt")
    if isinstance(p, str):
        selected = "<ide_selection" in p
        # VS Code puts context blocks (<ide_opened_file>, <ide_selection>, <browser_instruction>…) before the human's text
        p = re.sub(r"^(?:\s*<([a-z_]+)\b[^>]*>.*?</\1>)+", "", p, flags=re.S).strip()
        if not p.startswith("/"):
            i, j = 0, len(p)
            while i < j and unicodedata.category(p[i])[0] not in "LN":
                i += 1
            while j > i and unicodedata.category(p[j - 1])[0] not in "LN":
                j -= 1
            if p[i:j].casefold() in ("проще", "simpler"):
                out("[plain] The human's whole message is «проще»/\"simpler\": rewrite your last substantive reply (not a service line) "
                    "simpler and shorter — same facts, decisions and questions; you may explain a word or give an example, but add no new "
                    "facts or decisions. If that reply offered «проще»/\"simpler\" as an option, it is the answer. If it was a pre-work "
                    "understanding check, a plan or a shown result (mockup, document, draft for others)"
                    + (", or the human selected text in the editor" if selected else "")
                    + ", ask in one phrase what to simplify: the message"
                    + (", the thing itself or the selection." if selected else " or the thing itself."))
except Exception:
    pass
