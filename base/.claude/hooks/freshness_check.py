#!/usr/bin/env python3
"""SessionStart hook: surfaces overdue project-maintenance items so the model
cannot silently skip the start-of-session freshness check.

Reads wiki/log.md (last full `lint` run) and STATE.md (`_Обновлено:` date),
compares against today, and — ONLY when something is past threshold — emits a
SessionStart `additionalContext` payload instructing the model to raise it in
its first reply, in the user's language. Nothing stale → no output (silent),
matching the "стартовые проверки молчаливы, только при отклонении" rule.

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
    deviations = []

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

    if not deviations:
        return 0  # nothing stale → silent

    context = (
        "[start-of-session freshness check] These project-maintenance items are past "
        "threshold. In your FIRST reply, in the user's language, briefly surface ONLY "
        "these and offer to act — do not stay silent, do not list what is fine. If the "
        "user declines, drop it.\n" + "\n".join(deviations)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A maintenance hint must never break a session.
        sys.exit(0)
