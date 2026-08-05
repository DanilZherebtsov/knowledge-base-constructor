#!/usr/bin/env python3
"""SessionStart hook: surfaces overdue project-maintenance items so the model
cannot silently skip the start-of-session freshness check, and re-grounds the
model in project files right after a context compaction.

Reads wiki/log.md (last full `lint` run) and STATE.md (`_Обновлено:` date),
compares against today, and — ONLY when something is past threshold — emits a
SessionStart `additionalContext` payload instructing the model to raise it in
its first reply, in the user's language. Nothing stale → no output (silent),
matching the "стартовые проверки молчаливы, только при отклонении" rule.

Second job: when `source == "compact"` (the conversation was just replaced by a
summary), always emit a re-grounding note pointing at STATE.md, the open unit of
work and any live `tmp/<run>/` journal. Compaction fires unannounced, usually
mid-work, and drops exactly the operational detail (which item we stopped on,
what was already rejected); PreCompact cannot help — its stdout never reaches
the model — so the recovery happens on the far side of the boundary, here.

Locale-neutral: the injected text is model-facing English (never shown to the
user verbatim); the model translates when it speaks to the human. Identical
byte-for-byte in the RU and EN constructor mirrors.

Always exits 0 — a maintenance hint must never block a session.
"""
import json
import os
import re
import sys
from datetime import date, datetime

THRESHOLD_DAYS = 7  # keep in sync with CLAUDE.md discipline §5 and STATE freshness trigger


def project_root() -> str:
    # Claude Code sets CLAUDE_PROJECT_DIR for hooks; fall back to cwd.
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def hook_source() -> str:
    """How the session started: startup | resume | clear | compact | fork.

    Arrives as the `source` field of the SessionStart payload on stdin. Guarded
    on isatty() so a manual run in a terminal returns instead of blocking on a
    read that will never end.
    """
    if sys.stdin.isatty():
        return ""
    try:
        raw = sys.stdin.read()
    except Exception:
        return ""
    if not raw.strip():
        return ""
    try:
        return json.loads(raw).get("source") or ""
    except Exception:
        return ""


def live_tmp_runs(root: str, limit: int = 3):
    """Subfolders of tmp/ — one per long-pass run (see CLAUDE.md, `tmp/`)."""
    path = os.path.join(root, "tmp")
    if not os.path.isdir(path):
        return []
    try:
        names = sorted(
            n for n in os.listdir(path)
            if not n.startswith(".") and os.path.isdir(os.path.join(path, n))
        )
    except OSError:
        return []
    return names[:limit]


def compaction_note(root: str) -> str:
    """Model-facing note injected right after the conversation was summarized."""
    lines = [
        "[context was just compacted] Everything said before this point was replaced "
        "by a summary. Detail is gone — including where exactly the work stopped. "
        "Before continuing, re-ground in the project's files rather than in "
        "recollection, and do not reconstruct from the summary what is written down:",
        "- STATE.md — where we stopped, what is in progress, what comes next.",
        "- The open unit of work (its spec / task file), if one is active.",
        "- A long pass in flight — read its progress journal under tmp/<run>/ and "
        "resume from it: items already done are NOT redone, failures stay on the "
        "failures list.",
    ]
    runs = live_tmp_runs(root)
    if runs:
        lines.append("Working residue present now: " + ", ".join(f"tmp/{n}/" for n in runs) + ".")
    lines.append(
        "Say nothing about this note as such; just continue the work grounded in "
        "what you read."
    )
    return "\n".join(lines)


def parse_date(token: str):
    token = token.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def last_lint_date(root: str):
    """Most recent log entry whose operation label is exactly `lint`.

    Log lines look like `## [2026-06-30] lint | найдено N ...`. The first word
    after the date bracket is the operation; `lint-fix`, `feature + lint`,
    `wiki+fix` etc. are NOT full lint runs and must not count. The trailing
    lookahead (space / `|` / end-of-line) is what excludes `lint-fix` — a plain
    `\blint\b` would match it, since `-` is a word boundary.
    """
    path = os.path.join(root, "wiki", "log.md")
    if not os.path.isfile(path):
        return None  # not bootstrapped yet — stay silent
    pat = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s+lint(?:\s|\||$)")
    dates = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = pat.match(line)
            if m:
                d = parse_date(m.group(1))
                if d:
                    dates.append(d)
    return max(dates) if dates else None


def state_updated_date(root: str):
    path = os.path.join(root, "STATE.md")
    if not os.path.isfile(path):
        return None
    pat = re.compile(r"_Обновлено:\s*([0-9.\-]+)_")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = pat.search(line)
            if m:
                return parse_date(m.group(1))
    return None


def main() -> int:
    root = project_root()
    today = date.today()
    source = hook_source()
    blocks = []
    deviations = []

    if source == "compact":
        blocks.append(compaction_note(root))

    ld = last_lint_date(root)
    if ld is not None:
        age = (today - ld).days
        if age > THRESHOLD_DAYS:
            deviations.append(
                f"- Prophylaxis (lint / «профилактика»): last full run was {age} days ago "
                f"(threshold {THRESHOLD_DAYS}). Offer to run it."
            )

    sd = state_updated_date(root)
    if sd is not None:
        age = (today - sd).days
        if age > THRESHOLD_DAYS:
            deviations.append(
                f"- STATE.md: last updated {age} days ago (threshold {THRESHOLD_DAYS}). "
                f"Ask what changed / offer to refresh it."
            )

    if deviations:
        blocks.append(
            "[start-of-session freshness check] These project-maintenance items are past "
            "threshold. In your FIRST reply, in the user's language, briefly surface ONLY "
            "these and offer to act — do not stay silent, do not list what is fine. If the "
            "user declines, drop it.\n" + "\n".join(deviations)
        )

    if not blocks:
        return 0  # nothing stale, no compaction → silent

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(blocks),
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A maintenance hint must never break a session.
        sys.exit(0)
