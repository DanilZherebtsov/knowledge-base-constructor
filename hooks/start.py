#!/usr/bin/env python3
"""SessionStart hook — one block of context at the start of every session and right after a context compaction.

1. «Program: X. Its specifics: methodology/…/<x>.md» — so the agent knows its program without running anything.
2. The environment check (env_check.report): problems the human must hear, each as TELL THE HUMAN.
3. Maintenance past threshold: last full `lint` run, STATE.md freshness.
4. After a compaction (`source == "compact"`): re-ground in the project's files, not in the summary.

Model-facing English; the agent speaks to the human in their language. Identical in the RU and EN editions.
Always one JSON object (additionalContext) — the program line is always there."""
import os, re, sys
from datetime import date, datetime

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import env_check  # noqa: E402

THRESHOLD_DAYS = 7  # keep in sync with the AGENTS.md discipline and the STATE freshness trigger


def live_tmp_runs(limit=3):
    path = os.path.join(common.ROOT, "tmp")
    try:
        names = sorted(n for n in os.listdir(path) if not n.startswith(".") and n != "knowledge-check"
                       and os.path.isdir(os.path.join(path, n)))
    except OSError:
        return []
    return names[:limit]


def compaction_note():
    lines = [
        "[context was just compacted] Everything said before this point was replaced by a summary. Detail is gone — "
        "including where exactly the work stopped. Before continuing, re-ground in the project's files rather than in "
        "recollection, and do not reconstruct from the summary what is written down:",
        "- AGENTS.md — the project's rules (re-read the parts the current task touches).",
        "- STATE.md — where we stopped, what is in progress, what comes next.",
        "- The open unit of work (its spec / task file), if one is active.",
        "- A long pass in flight — read its progress journal under tmp/<run>/ and resume from it: items already done are "
        "NOT redone, failures stay on the failures list.",
    ]
    runs = live_tmp_runs()
    if runs:
        lines.append("Working residue present now: " + ", ".join("tmp/%s/" % n for n in runs) + ".")
    lines.append("Say nothing about this note as such; just continue the work grounded in what you read.")
    return "\n".join(lines)


def parse_date(token):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(token.strip(), fmt).date()
        except ValueError:
            continue
    return None


def log_dates(only_lint):
    """Dates of wiki/log.md entries — `## [YYYY-MM-DD] lint | …` exactly (not `lint-fix`), or any dated entry."""
    path = os.path.join(common.ROOT, "wiki", "log.md")
    if not os.path.isfile(path):
        return []
    pat = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]" + (r"\s+lint(?:\s|\||$)" if only_lint else ""))
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = pat.match(line)
            if m and parse_date(m.group(1)):
                out.append(parse_date(m.group(1)))
    return out


def state_updated():
    """The `_Обновлено:` / `_Updated:` marker, anchored at the line start (index lines carry `_Updated:` too)."""
    path = os.path.join(common.ROOT, "STATE.md")
    if not os.path.isfile(path):
        return None
    pat = re.compile(r"^_(?:Обновлено|Updated):\s*([0-9.\-]+)_")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = pat.search(line)
            if m:
                return parse_date(m.group(1))
    return None


def freshness():
    today, out = date.today(), []
    lint = log_dates(True)
    if lint:
        age = (today - max(lint)).days
        if age > THRESHOLD_DAYS:
            out.append("- Maintenance (the `lint` operation): last full run was %d days ago (threshold %d). Offer to run it."
                       % (age, THRESHOLD_DAYS))
    else:
        first = log_dates(False)
        if first and (today - first[0]).days > THRESHOLD_DAYS:
            out.append("- Maintenance (the `lint` operation) has not run yet; the project was started %d days ago "
                       "(threshold %d). Offer to run it." % ((today - first[0]).days, THRESHOLD_DAYS))
    sd = state_updated()
    if sd and (today - sd).days > THRESHOLD_DAYS:
        out.append("- STATE.md: last updated %d days ago (threshold %d). Ask what changed / offer to refresh it."
                   % ((today - sd).days, THRESHOLD_DAYS))
    return out


def main():
    prog = common.program()
    event = common.read_event()
    common.mark_alive(prog, "start")
    loc = common.locale()
    blocks = ["[environment] Program: %s. Its specifics (setup, checks, updates, subagents, deletion): %s — read the "
              "relevant section when a task touches them. The project's rules are in AGENTS.md."
              % (env_check.NAMES[prog], common.program_file(prog, loc))]
    try:
        problems = env_check.report(prog, loc, from_hook=True)
    except Exception as ex:
        common.log_error(prog, "start: env_check: %r" % ex)
        problems = []
    if problems:
        blocks.append("[environment check] Tell the human each item below at the top of your FIRST reply, in their language, "
                      "with what to do, then carry on with their request as usual. Hold off on the task only where an item "
                      "itself says not to take one on:\n"
                      + "\n".join("- " + p for p in problems))
    if event.get("source") == "compact":
        blocks.append(compaction_note())
    try:
        stale = freshness()
    except Exception as ex:  # a broken STATE.md or log must not take the environment line with it
        common.log_error(prog, "start: freshness: %s" % type(ex).__name__)
        stale = []
    if stale:
        blocks.append("[start-of-session freshness check] These project-maintenance items are past threshold. In your FIRST "
                      "reply, in the user's language, briefly surface ONLY these and offer to act — do not stay silent, do "
                      "not list what is fine. If the user declines, drop it.\n" + "\n".join(stale))
    common.context("SessionStart", "\n\n".join(blocks))


if __name__ == "__main__":
    common.run(main)
