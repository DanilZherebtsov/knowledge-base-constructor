#!/usr/bin/env python3
"""PreToolUse hook — raw/ guard: sources in raw/ are never changed by the agent, even on the human's direct request.
Denies editing, overwriting, moving and deleting an existing file under raw/. Allowed: new files; appending a new
item at the end of the conversation note in raw/из-чата/ (raw/from-chat/); moving a file from the raw/ root into
input/ or a raw/ subfolder; renaming files that agents apply as instructions (CLAUDE.md, AGENTS.md, .claude/ …)
inside their folder — the safety step of ingest; .gitkeep. Shell commands are checked by patterns, and read-only
listings (find, grep -l, ls) feeding a destructive command are run dry to see what they would hit — the rule in
CLAUDE.md stays the main line, this is the seatbelt. Only ever answers "deny"; any failure of the hook lets the
call through. Layout: the event layer (Claude Code tool calls -> operations) is separate from the policy, so an
adapter for another program replaces only the event layer."""
import glob, json, os, re, shlex, subprocess, sys, unicodedata

NOTE_DIRS = ("из-чата", "from-chat")
NOT_SOURCES = {".gitkeep", ".DS_Store", "Thumbs.db", "desktop.ini"}
DELETERS = {"rm", "unlink", "shred", "truncate", "trash"}
INPLACE = {"sed", "gsed", "perl", "awk", "gawk"}
WRITERS = DELETERS | INPLACE | {"mv", "cp", "tee", "ln", "install", "rsync", "ditto", "dd"}
SHELLS = {"sh", "bash", "zsh", "dash"}
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "time"}
PREFIXES = {"sudo", "command", "builtin", "exec", "nohup", "env", "nice", "timeout", "caffeinate", "xcrun"}
ENDERS = {"done", "fi", "}", "esac"}
AGENT_FILES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "QWEN.md", "GEMINI.md", ".cursorrules", ".windsurfrules",
               ".clinerules", "copilot-instructions.md", ".claude", ".codex", ".qwen", ".gemini", ".cursor"}
LISTERS = {"find", "grep", "egrep", "rg", "ls", "fd", "git"}
XARGS_OPTS_WITH_VALUE = {"-n", "-I", "-i", "-L", "-l", "-P", "-d", "-E", "-e", "-s", "-J", "-R", "-a"}
RAW_WORD = re.compile(r"(^|[\s/'\"=])raw(/|$|[\s'\"])")


def nfc(p):
    return unicodedata.normalize("NFC", p)


class Denied(Exception):
    def __init__(self, path, why):
        self.path, self.why = path, why


# ---------------------------------------------------------------- policy

class Policy:
    def __init__(self, root):
        self.root = nfc(os.path.realpath(root))
        self.raw = nfc(os.path.realpath(os.path.join(root, "raw")))
        self.input = nfc(os.path.realpath(os.path.join(root, "input")))
        self.raw_key = self.raw.casefold()

    def p(self, path):
        return nfc(os.path.realpath(path))

    def link(self, path):
        """The path itself when it is a symlink (deleting or moving a link touches only the link)."""
        return nfc(os.path.join(os.path.realpath(os.path.dirname(path) or "."), os.path.basename(path))) \
            if os.path.islink(path) else self.p(path)

    def in_raw(self, p):
        return p.casefold().startswith(self.raw_key + os.sep)

    def is_raw(self, p):
        return p.casefold() == self.raw_key

    def above_raw(self, p):
        return self.raw_key.startswith(p.casefold().rstrip(os.sep) + os.sep)

    def top_of_raw(self, p):
        return os.path.dirname(p).casefold() == self.raw_key

    def in_notes(self, p):
        rel = os.path.relpath(p, self.raw).split(os.sep)
        return self.in_raw(p) and len(rel) >= 2 and rel[0] in NOTE_DIRS

    def source(self, p):
        """An existing file under raw/ that is a source (not a placeholder or a system file, not a symlink)."""
        return self.in_raw(p) and os.path.isfile(p) and not os.path.islink(p) and os.path.basename(p) not in NOT_SOURCES

    def sources_under(self, p):
        """First source at p or below it; also when p contains raw/."""
        if self.source(p):
            return p
        if os.path.isdir(p) and not os.path.islink(p) and (self.in_raw(p) or self.is_raw(p) or self.above_raw(p)):
            base = self.raw if self.above_raw(p) else p
            for d, _, files in os.walk(base):
                for f in files:
                    q = self.p(os.path.join(d, f))
                    if self.source(q):
                        return q
        return None

    # operations
    def write(self, path, new=None, append_only=False):
        """Overwrite or edit `path`. new: full new text if known. append_only: the operation only appends."""
        p = self.p(path)
        if not self.source(p):
            return
        if new is not None:
            try:
                with open(p, encoding="utf-8") as f:
                    old = f.read()
            except Exception:
                old = None
            if old is not None and new == old:
                return
            if old is not None and self.in_notes(p) and appends(old, new):
                return
        elif append_only and self.in_notes(p):
            return
        raise Denied(p, "edit")

    def delete(self, path):
        lp = self.link(path)
        if os.path.islink(path):
            return
        q = self.sources_under(lp)
        if q:
            raise Denied(q, "delete")

    def move(self, src, dst):
        if os.path.islink(src):
            return
        s, d = self.p(src), self.p(dst)
        target = os.path.join(d, os.path.basename(s)) if os.path.isdir(d) else d
        self.write(target)
        q = self.sources_under(s)
        if not q:
            return
        out_of_top = os.path.isfile(s) and self.top_of_raw(s) and (
            os.path.dirname(target) == self.input or (self.in_raw(target) and not self.top_of_raw(target)))
        defused = os.path.basename(s) in AGENT_FILES and os.path.dirname(target) == os.path.dirname(s) \
            and os.path.basename(target) not in AGENT_FILES
        if not (out_of_top or defused):
            raise Denied(q, "move")

    def copy_into(self, src, dst, contents=False):
        """cp/rsync/install/ln/ditto: nothing under raw/ may be overwritten. contents: copy what is inside src."""
        s, d = self.p(src), self.p(dst)
        if not (self.in_raw(d) or self.is_raw(d) or self.above_raw(d)):
            return
        if os.path.isdir(s):
            base = d if contents or not os.path.isdir(d) else os.path.join(d, os.path.basename(s.rstrip(os.sep)))
            for dd, _, files in os.walk(s):
                for f in files:
                    self.write(os.path.join(base, os.path.relpath(os.path.join(dd, f), s)))
            return
        self.write(os.path.join(d, os.path.basename(s)) if os.path.isdir(d) else d)


def appends(old, new):
    """new = old + a new block: the old text unchanged, the addition starts on a new line."""
    if not new.startswith(old):
        return False
    return old == "" or old.endswith("\n") or new[len(old)] == "\n"


# ---------------------------------------------------------------- shell patterns

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.S)


def tokens(cmd):
    cmd = cmd.replace("\\\n", " ")
    cmd = HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], cmd).replace("\n", " ; ")
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return re.sub(r"(;|&&|\|\||\||>>|>)", r" \1 ", cmd).split()


def commands(cmd):
    out, cur = [], []
    for t in tokens(cmd):
        if t and set(t) <= set(";&|()") and t not in ("&>", ">&"):
            if cur:
                out.append((cur, t))
            cur = []
        elif t in ENDERS and not cur:
            continue
        else:
            cur.append(t)
    if cur:
        out.append((cur, ""))
    return out


def strip_prefix(words):
    while words and (words[0] in KEYWORDS or words[0] in PREFIXES or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
        if words[0] == "timeout" and len(words) > 1 and re.match(r"^\d", words[1]):
            words = words[1:]
        words = words[1:]
    if words and words[0] in ("for", "case", "select"):
        return []
    return words


def expand(cwd, token):
    token = os.path.expanduser(os.path.expandvars(token.replace("$PWD", cwd).replace("${PWD}", cwd)))
    full = token if os.path.isabs(token) else os.path.join(cwd, token)
    if any(c in token for c in "*?["):
        hits = glob.glob(full, recursive="**" in token)
        if hits:
            return hits
    return [full]


def run_git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, "-c", "core.quotePath=false", *args], capture_output=True, timeout=5,
                           env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        return r.stdout.decode("utf-8", "replace").splitlines() if r.returncode == 0 else []
    except Exception:
        return []


def dry_list(words, cwd):
    """Run a read-only listing (find without its action, grep -l, ls, git ls-files) and return the paths it prints."""
    prog = os.path.basename(words[0])
    if prog == "find":
        args = []
        skip = 0
        for k, a in enumerate(words[1:]):
            if skip:
                skip -= 1
                continue
            if a in ("-delete", "-print0", "-print", "-ls"):
                continue
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                rest = words[1 + k + 1:]
                end = next((i for i, x in enumerate(rest) if x in (";", "\\;", "+")), len(rest))
                skip = end + 1
                continue
            args.append(a)
        argv = ["find", *args, "-print"]
    elif prog in ("grep", "egrep", "rg") and any(re.match(r"^-[A-Za-z]*l", a) or a == "--files-with-matches" for a in words[1:]):
        nonopt = [k for k, a in enumerate(words) if k and not a.startswith("-")]
        argv = words[:nonopt[0] + 1] + [x for a in words[nonopt[0] + 1:] for x in (expand(cwd, a) if not a.startswith("-") else [a])] \
            if nonopt else words
    elif prog in ("ls", "fd"):
        argv = [words[0]] + [x for a in words[1:] for x in (expand(cwd, a) if not a.startswith("-") else [a])]
    elif prog == "git" and "ls-files" in words:
        argv = words
    else:
        return None
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=5)
        return [os.path.join(cwd, x) for x in r.stdout.decode("utf-8", "replace").splitlines() if x]
    except Exception:
        return None


def subst(words, env):
    out = []
    for w in words:
        m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?(.*)", w)
        if m and m.group(1) in env:
            out += [v + m.group(2) for v in env[m.group(1)]]
        else:
            out.append(w)
    return out


def touches_raw(text):
    return bool(RAW_WORD.search(text))


def check_shell(pol, cmd, cwd, depth=0):
    upstream, upstream_text = None, ""
    loop_feed = None  # a pipe into `while read …`: what it feeds reaches the loop body
    env = {}
    for words, sep in commands(cmd):
        if len(words) >= 3 and words[0] in ("for", "select") and words[2] == "in":
            env[words[1]] = [x for v in words[3:] for x in expand(cwd, v)]
            continue
        if len(words) == 1 and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=[^$`(]*$", words[0]):
            k, v = words[0].split("=", 1)
            env[k] = [v]
            continue
        words = subst(words, env)
        args, i = [], 0
        while i < len(words):
            w = words[i]
            m = re.match(r"^(\d*|&)(>>|>\||>)(.*)$", w)
            if m:
                op, rest = m.group(2), m.group(3)
                target = rest or (words[i + 1] if i + 1 < len(words) else "")
                i += 1 if rest else 2
                if target and not target.startswith("&"):
                    for p in expand(cwd, target):
                        pol.write(p, append_only=(op == ">>"))
                continue
            if w in ("<", "<<", "<<<"):
                i += 2
                continue
            args.append(w)
            i += 1
        if args and args[0] in ("while", "until") and (upstream or upstream_text):
            loop_feed = (upstream, upstream_text)
        args = strip_prefix(args)
        if args:
            feed = loop_feed or (upstream, upstream_text)
            check_one(pol, args, cwd, feed[0], feed[1], depth)
            if os.path.basename(args[0]) in ("cd", "pushd"):
                cwd = expand(cwd, args[1])[0] if len(args) > 1 else os.path.expanduser("~")
        if sep == "|":
            listed = dry_list(args, cwd) if args and os.path.basename(args[0]) in LISTERS else None
            upstream = (upstream or []) + (listed or [])
            upstream_text += " " + " ".join(args) if listed is None else ""
        else:
            upstream, upstream_text = None, ""


def check_one(pol, args, cwd, upstream, upstream_text, depth):
    prog, rest = os.path.basename(args[0]), args[1:]
    paths = [a for a in rest if not a.startswith("-")]

    def unresolved_from_pipe():
        # a destructive command fed by a pipe it cannot see (while read, $(…)): deny if the feed names raw/
        if touches_raw(upstream_text) or any(pol.in_raw(pol.p(x)) for x in (upstream or [])):
            q = pol.sources_under(pol.raw)
            if q:
                raise Denied(q, "shell")

    if prog in SHELLS or prog == "eval":
        inner = rest[rest.index("-c") + 1] if "-c" in rest and rest.index("-c") + 1 < len(rest) else (
            " ".join(rest) if prog == "eval" else None)
        if inner and depth < 3:
            check_shell(pol, inner, cwd, depth + 1)
    elif prog == "xargs":
        j = 0
        while j < len(rest) and rest[j].startswith("-"):
            j += 2 if rest[j] in XARGS_OPTS_WITH_VALUE else 1
        inner = rest[j:]
        if inner and os.path.basename(inner[0]) in WRITERS:
            if upstream:
                for x in upstream:
                    for q in (pol.sources_under(pol.p(x)),):
                        if q:
                            raise Denied(q, "shell")
            if touches_raw(upstream_text) or any(touches_raw(a) for a in inner):
                q = pol.sources_under(pol.raw)
                if q:
                    raise Denied(q, "shell")
    elif prog == "git" and rest:
        check_git(pol, rest, cwd)
    elif prog == "find":
        destructive = "-delete" in rest or any(
            a in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(rest) and os.path.basename(rest[k + 1]) in WRITERS
            for k, a in enumerate(rest))
        if destructive:
            hits = dry_list(args, cwd)
            if hits is None:
                hits = [x for a in rest[:1] for x in expand(cwd, a)]
            for x in hits:
                q = pol.sources_under(pol.p(x)) if not os.path.isdir(x) or pol.in_raw(pol.p(x)) else (
                    pol.p(x) if pol.source(pol.p(x)) else None)
                if q:
                    raise Denied(q, "shell")
    elif prog in DELETERS:
        for a in paths:
            if "$" in a or "`" in a:
                unresolved_from_pipe()
            for p in expand(cwd, a):
                pol.delete(p) if prog != "truncate" else pol.write(p)
    elif prog in INPLACE:
        if any(re.match(r"^-[A-Za-z]*i", a) or a.startswith("--in-place") for a in rest) or "inplace" in rest:
            for a in paths:
                if "$" in a or "`" in a:
                    unresolved_from_pipe()
                for p in expand(cwd, a):
                    pol.write(p)
    elif prog == "tee":
        for a in paths:
            for p in expand(cwd, a):
                pol.write(p, append_only=("-a" in rest or "--append" in rest))
    elif prog == "dd":
        for a in rest:
            if a.startswith("of="):
                for p in expand(cwd, a[3:]):
                    pol.write(p)
    elif prog in ("curl", "wget"):
        for k, a in enumerate(rest):
            if k + 1 < len(rest) and ((prog == "curl" and a in ("-o", "--output")) or a in ("-O", "--output-document")):
                for p in expand(cwd, rest[k + 1]):
                    pol.write(p)
    elif prog == "patch":
        for a in paths:
            for p in expand(cwd, a):
                pol.write(p)
    elif prog == "mv" and len(paths) >= 2:
        for s in paths[:-1]:
            if "$" in s:
                unresolved_from_pipe()
            for sp in expand(cwd, s):
                pol.move(sp, expand(cwd, paths[-1])[0])
    elif prog in ("cp", "rsync", "install", "ln", "ditto") and len(paths) >= 2:
        if prog == "ln" and not any(a.startswith("-") and "f" in a for a in rest):
            return
        for s in paths[:-1]:
            contents = prog in ("rsync", "ditto") and s.endswith("/") or s.endswith("/.") or prog == "ditto"
            for sp in expand(cwd, s):
                pol.copy_into(sp, expand(cwd, paths[-1])[0], contents=contents)


def check_git(pol, rest, cwd):
    j = 0
    while j < len(rest) and rest[j].startswith("-"):
        if rest[j] == "-C" and j + 1 < len(rest):
            cwd = expand(cwd, rest[j + 1])[0]
        j += 2 if rest[j] in ("-C", "-c") else 1
    if j >= len(rest):
        return
    sub, opts = rest[j], rest[j + 1:]
    dashdash = opts.index("--") if "--" in opts else None
    pathspec = opts[dashdash + 1:] if dashdash is not None else []
    words = [a for a in (opts[:dashdash] if dashdash is not None else opts) if not a.startswith("-")]
    raw_rel = os.path.relpath(pol.raw, cwd)

    def changed_in_raw(*extra):
        return run_git(cwd, "status", "--porcelain", "--untracked-files=no", *extra, "--", raw_rel)

    def scope_hits_raw(targets):
        """Does any explicit target reach raw/? No targets — the whole tree, so yes."""
        if not targets:
            return True
        for t in targets:
            for p in expand(cwd, t):
                p = pol.p(p)
                if pol.in_raw(p) or pol.is_raw(p) or pol.above_raw(p):
                    return True
        return False

    if sub == "mv":
        targets = words
        if len(targets) >= 2:
            for s in targets[:-1]:
                for sp in expand(cwd, s):
                    pol.move(sp, expand(cwd, targets[-1])[0])
    elif sub == "rm" and "--cached" not in opts:
        for a in words + pathspec:
            for p in expand(cwd, a):
                pol.delete(p)
    elif sub == "clean" and any(o.startswith("-") and not o.startswith("--") and "f" in o or o == "--force" for o in opts):
        dry = [o.replace("f", "n") if o.startswith("-") and not o.startswith("--") else o for o in opts if o != "--force"]
        if not any(o.startswith("-") and not o.startswith("--") and "n" in o for o in dry):
            dry = ["-n"] + dry
        for line in run_git(cwd, "clean", *dry):
            name = line.split(" ", 2)[-1] if line.startswith("Would remove ") else None
            if name:
                q = pol.sources_under(pol.p(os.path.join(cwd, name)))
                if q:
                    raise Denied(q, "shell")
    elif sub in ("checkout", "restore"):
        if sub == "restore" and "--staged" in opts and not {"--worktree", "-W"} & set(opts):
            return
        source = any(o.startswith("--source") or o == "-s" for o in opts)
        if sub == "checkout" and dashdash is None:
            files = [w for w in words if os.path.exists(os.path.join(cwd, w))]
            if not files:
                return
            targets, rev = files, len(words) > len(files)
        else:
            targets, rev = (pathspec or words), (sub == "checkout" and bool(words)) or source
        if not scope_hits_raw(targets):
            return
        if rev:
            for t in targets:
                for p in expand(cwd, t):
                    q = pol.sources_under(pol.p(p)) if not pol.above_raw(pol.p(p)) else pol.sources_under(pol.raw)
                    if q:
                        raise Denied(q, "shell")
        elif changed_in_raw():
            raise Denied(pol.raw + os.sep + "…", "shell")
    elif sub == "reset" and "--hard" in opts:
        if changed_in_raw() or (words and run_git(cwd, "diff", "--name-only", words[0], "HEAD", "--", raw_rel)):
            raise Denied(pol.raw + os.sep + "…", "shell")
    elif sub == "revert":
        for c in words:
            if run_git(cwd, "diff", "--name-only", c + "^", c, "--", raw_rel):
                raise Denied(pol.raw + os.sep + "…", "shell")
    elif sub == "stash" and not (words and words[0] in ("list", "show", "pop", "apply", "drop", "branch", "clear")):
        if not scope_hits_raw(pathspec):
            return
        extra = ["--untracked-files=all"] if ({"-u", "--include-untracked", "-a", "--all"} & set(opts)) else []
        if extra and run_git(cwd, "status", "--porcelain", *extra, "--", raw_rel):
            raise Denied(pol.raw + os.sep + "…", "shell")
        if changed_in_raw():
            raise Denied(pol.raw + os.sep + "…", "shell")


# ---------------------------------------------------------------- event layer: Claude Code

def check_claude_event(pol, data):
    name, ti = data.get("tool_name"), data.get("tool_input") or {}
    cwd = data.get("cwd") or pol.root
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path")
        if not isinstance(path, str):
            return
        path = os.path.join(cwd, os.path.expanduser(path))
        new = None
        if name == "Write":
            new = ti.get("content") if isinstance(ti.get("content"), str) else None
        elif name in ("Edit", "MultiEdit"):
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for e in (ti.get("edits") or []) if name == "MultiEdit" else [ti]:
                    a, b = e.get("old_string", ""), e.get("new_string", "")
                    if not a or a not in text:
                        raise ValueError
                    text = text.replace(a, b) if e.get("replace_all") else text.replace(a, b, 1)
                new = text
            except Exception:
                new = None
        pol.write(path, new=new)
    elif name in ("Bash", "Monitor"):
        cmd = ti.get("command")
        if isinstance(cmd, str):
            check_shell(pol, cmd, os.path.realpath(cwd))


def deny_claude(pol, d):
    rel = os.path.relpath(d.path, pol.root)
    msg = ("raw/ guard: this call would " + {"edit": "change", "delete": "delete", "move": "move", "shell": "change or delete"}[d.why]
           + " " + rel + ", a source in raw/. The agent never edits, overwrites, renames, moves or deletes an existing raw/ "
           "file, even on the human's direct request (CLAUDE.md, «`raw/` неизменяем» / \"`raw/` is immutable\"). "
           "If the call was aimed at raw/: do not retry it with another tool or command; tell the human that only they can "
           "change a source by hand, and offer to record their correction as knowledge from the conversation (a note in "
           "raw/из-чата/ or raw/from-chat/ and a wiki page citing it and the source). If the call was not aimed at raw/ "
           "(a broad path, pattern or project-wide edit caught it), narrow it so it leaves raw/ out. Allowed in raw/: new "
           "files; a new item at the end of the conversation note; moving a file (not a folder) from the raw/ root into "
           "input/ or a raw/ subfolder; renaming agent instruction files inside their folder; .gitkeep. Header and secret "
           "removal are done in input/ before placing (methodology/ingest.md, «Что можно в `raw/`» / \"What is allowed in "
           "`raw/`\").")
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                         "permissionDecisionReason": msg}}))


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        pol = Policy(root)
        if not os.path.isdir(pol.raw):
            return
        try:
            check_claude_event(pol, data)
        except Denied as d:
            deny_claude(pol, d)
    except Exception:
        pass


if __name__ == "__main__":
    main()
