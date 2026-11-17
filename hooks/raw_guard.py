#!/usr/bin/env python3
"""PreToolUse hook — raw/ guard: sources in raw/ are never changed by the agent, even on the human's direct request.
Denies editing, overwriting, moving and deleting an existing file under raw/. Allowed: new files; appending a new
item at the end of the conversation note in raw/из-чата/ (raw/from-chat/); moving a file from the raw/ root into
input/ or a raw/ subfolder; renaming files that agents apply as instructions (CLAUDE.md, AGENTS.md, .claude/ …)
inside their folder — the safety step of ingest; .gitkeep. Shell commands are checked by patterns, and read-only
listings (find, grep -l, ls) feeding a destructive command are run dry to see what they would hit — the rule in
AGENTS.md stays the main line, this is the seatbelt. Only ever answers "deny"; any failure of the hook lets the
call through. Layout: the policy is separate from the event layers (Claude Code, Qwen Code, Codex tool calls -> operations);
called as `python3 hooks/raw_guard.py --program claude|codex|qwen`; the project root is the folder above hooks/."""
import glob, json, os, re, shlex, shutil, subprocess, sys, unicodedata

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
    if os.name == "nt":  # Git Bash paths: /c/Users/… and /cygdrive/c/… are C:/Users/…
        m = re.match(r"^/(?:cygdrive/)?([A-Za-z])(/.*)?$", token)
        if m:
            token = m.group(1) + ":" + (m.group(2) or "/")
    token = os.path.expanduser(os.path.expandvars(token.replace("$PWD", cwd).replace("${PWD}", cwd)))
    full = token if os.path.isabs(token) else os.path.join(cwd, token)
    if any(c in token for c in "*?["):
        hits = glob.glob(full, recursive="**" in token)
        if hits:
            return hits
    return [full]


def _which(name, not_system=False):
    import common  # main() has put hooks/ on sys.path
    return common.which(name, not_system)


def run_git(cwd, *args):
    git = _which("git")
    if not git:
        return []
    try:
        r = subprocess.run([git, "-C", cwd, "-c", "core.quotePath=false", *args], capture_output=True, timeout=5,
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
    exe = _which(prog, not_system=True)  # the system's own listing tool from PATH, never from the agent's command or folder (it runs before approval)
    if not exe or os.path.dirname(words[0]):
        return None
    try:
        r = subprocess.run([exe, *argv[1:]], cwd=cwd, capture_output=True, timeout=5)
        listed = [os.path.join(cwd, x) for x in r.stdout.decode("utf-8", "replace").splitlines() if x]
        # a listing that failed and printed nothing (a foreign find.exe…) is not "nothing found"; one that printed
        # paths and then stumbled (a missing start folder, an unreadable file) still names what it would hit
        if r.returncode and not listed and not (prog in ("grep", "egrep", "rg") and r.returncode == 1):
            return None
        return listed
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
        ci = next((k for k, a in enumerate(rest) if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a)), None)  # -c, -lc, -euc …
        k = ci + 1 if ci is not None else None
        while k is not None and k < len(rest) and rest[k] == "--":
            k += 1
        inner = rest[k] if k is not None and k < len(rest) else (" ".join(rest) if prog == "eval" else None)
        if inner and depth < 3:
            check_shell(pol, inner, cwd, depth + 1)
    elif re.sub(r"\.exe$", "", prog.lower()) in ("powershell", "pwsh", "cmd") and depth < 3:
        check_pwsh(pol, " ".join([prog] + ['"%s"' % x if " " in x else x for x in rest]), cwd, depth + 1)
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
            if hits is None:  # no listing to run (no Unix find here): whatever lies under a start folder counts
                k = 0
                while k < len(rest) and rest[k] in ("-H", "-L", "-P"):
                    k += 1
                starts = []
                for a in rest[k:]:
                    if a.startswith(("-", "(", "!")):
                        break
                    starts.append(a)
                for x in [x for a in (starts or ["."]) for x in expand(cwd, a)]:
                    q = pol.sources_under(pol.p(x))
                    if q:
                        raise Denied(q, "shell")
                hits = []
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
    try:
        raw_rel = os.path.relpath(pol.raw, cwd)
    except ValueError:  # Windows: cwd on another drive
        raw_rel = pol.raw

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


# ---------------------------------------------------------------- event layers: Claude Code, Qwen Code, Codex

# --- PowerShell and cmd (Windows): a rough rule, not a parser -------------------------------------------------------
# Verbs that change files; every token that leads into raw/ is a target; a destructive verb whose target can't be seen
# (a pipe, $_, a variable, .NET, Invoke-Expression) is denied when the command names raw/ and raw/ has sources.
# A false deny costs the agent one rephrasing; a false allow costs a spoiled source.
PS_DELETE = {"remove-item", "rm", "del", "erase", "rd", "rmdir", "ri"}
PS_MOVE = {"move-item", "mv", "move", "mi"}
PS_RENAME = {"rename-item", "ren", "rename", "rni"}
PS_COPY = {"copy-item", "cp", "copy", "cpi", "xcopy", "robocopy"}
PS_WRITE = {"set-content", "sc", "out-file", "clear-content", "clc", "new-item", "ni", "tee-object", "tee"}
PS_APPEND = {"add-content", "ac"}
PS_CHANGERS = PS_DELETE | PS_MOVE | PS_RENAME | PS_COPY | PS_WRITE | PS_APPEND
PS_PATH = {"-path", "-literalpath", "-filepath", "-pspath", "-lp"}
PS_VALUE = PS_PATH | {"-value", "-encoding", "-itemtype", "-type", "-filter", "-include", "-exclude", "-stream", "-credential",
                      "-inputobject", "-delimiter", "-width", "-newname", "-name", "-destination", "-outfile", "-target"}
PS_RAW = re.compile(r"(?i)(^|[\s/\\'\"=:(,])raw([/\\]|$|[\s'\";),|])")
PS_OPAQUE = re.compile(r"(?i)\[(?:system\.)?io\.(?:file|directory)\]::(?:write|append|delete|move|copy|replace|create)|"
                       r"\b(?:iex|invoke-expression|invoke-command|start-process)\b|"
                       r"\.(?:delete|moveto|copyto|writealltext|writeallbytes|appendalltext)\s*\(")
PS_LIST = {"get-childitem", "gci", "dir", "ls", "forfiles"}
PS_RECURSE = {"-recurse", "-r", "/s"}


def ps_segments(cmd):
    """Statements of a PowerShell or cmd line: lists of tokens (quotes kept), split at ; | & && || { } ( ) and newlines."""
    segs, cur, tok, q, i = [], [], "", None, 0
    def flush_tok():
        nonlocal tok
        if tok:
            cur.append(tok)
        tok = ""
    def flush_seg():
        nonlocal cur
        flush_tok()
        if cur:
            segs.append(cur)
        cur = []
    while i < len(cmd):
        ch = cmd[i]
        if q:
            tok += ch
            if ch == "`" and q == '"' and i + 1 < len(cmd):
                tok += cmd[i + 1]
                i += 1
            elif ch == q:
                if q == "'" and cmd[i + 1:i + 2] == "'":
                    tok += "'"
                    i += 1
                else:
                    q = None
        elif ch in "'\"":
            q = ch
            tok += ch
        elif ch == "`" and i + 1 < len(cmd):
            tok += cmd[i + 1]
            i += 1
        elif ch in " \t\r":
            flush_tok()
        elif ch in ";|&{}()\n":
            flush_seg()
        elif ch == ">":
            op = tok if tok in ("", "1", "2", "3", "4", "5", "6", "*") else None
            if op is None:
                flush_tok()
                op = ""
            tok = ""
            op += ">"
            if cmd[i + 1:i + 2] == ">":
                op += ">"
                i += 1
            if cmd[i + 1:i + 2] == "&":  # 2>&1
                op += cmd[i + 1:i + 3]
                i += 2
            cur.append(op)
        else:
            tok += ch
        i += 1
    flush_seg()
    return segs


def ps_unquote(t):
    return t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t


def ps_paths(cwd, tok, env):
    """Paths a token names, or None when they can't be known (a variable, $_, a subexpression)."""
    t = ps_unquote(tok)
    t = re.sub(r"(?i)\$\{env:(\w+)\}|\$env:(\w+)", lambda m: os.environ.get(m.group(1) or m.group(2), "\0"), t)
    t = re.sub(r"%(\w+)%", lambda m: os.environ.get(m.group(1), "\0"), t)
    t = re.sub(r"(?i)\$(pwd|home)\b", lambda m: cwd if m.group(1).lower() == "pwd" else os.path.expanduser("~"), t)
    t = re.sub(r"\$(\w+)", lambda m: env.get(m.group(1).lower(), "\0"), t)
    if "\0" in t or "$" in t or "@(" in t:
        return None
    if os.sep == "/":
        t = t.replace("\\", "/")
    return expand(cwd, t) if t else []


def check_pwsh(pol, cmd, cwd, depth=0, from_bash=False):
    """from_bash: a Bash-tool command of Codex or Qwen on Windows, read as PowerShell/cmd too — heredoc bodies are data."""
    if from_bash:
        cmd = HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], cmd)
    over_raw = []  # a recursive listing or deletion starts at raw/ or above it

    def any_source():
        q = pol.sources_under(pol.raw)
        if q:
            raise Denied(q, "shell")

    names_raw = bool(PS_RAW.search(cmd))
    if names_raw and PS_OPAQUE.search(cmd):
        any_source()
    for b64 in re.findall(r"(?i)(?:^|\s)-e(?:c|nc|ncodedcommand)?\s+([A-Za-z0-9+/=]{8,})", cmd):
        try:
            inner = __import__("base64").b64decode(b64).decode("utf-16-le")
        except Exception:
            inner = ""
        if depth < 3 and inner:
            check_pwsh(pol, inner, cwd, depth + 1)
    env = {}
    for seg in ps_segments(cmd):
        words, i = [], 0
        while i < len(seg):  # redirections first: > and >> write their target
            w = seg[i]
            m = re.fullmatch(r"([0-9*]?)(>>?)(&\d)?", w)
            if m:
                if not m.group(3) and i + 1 < len(seg) and ps_unquote(seg[i + 1]).lower() not in ("$null", "nul"):
                    for p in ps_paths(cwd, seg[i + 1], env) or []:
                        pol.write(p, append_only=(m.group(2) == ">>"))
                i += 1 if m.group(3) else 2
                continue
            words.append(w)
            i += 1
        while words and words[0] in ("&", "."):
            words = words[1:]
        if not words:
            continue
        a = re.fullmatch(r"\$(\w+)\s*=?(.*)", words[0])  # $f = 'raw\a.md'
        if a and (a.group(2) or (len(words) > 1 and words[1] == "=")):
            val = a.group(2) or (words[2] if len(words) > 2 else "")
            val = val[1:] if val.startswith("=") else val
            env[a.group(1).lower()] = ps_unquote(val) if "$" not in ps_unquote(val) and "(" not in val else "\0"
            continue
        verb = os.path.basename(ps_unquote(words[0]).replace("\\", "/")).lower()
        verb = verb[:-4] if verb.endswith((".exe", ".cmd", ".bat")) else verb
        rest = words[1:]
        if verb == "set" and rest and "=" in rest[0] and not rest[0].startswith("-"):  # cmd: set X=...
            k, v = ps_unquote(" ".join(rest)).split("=", 1)
            env[k.strip().lower()] = v
            continue
        if verb in ("powershell", "pwsh", "cmd") and depth < 3:
            ci = next((k for k, x in enumerate(rest) if x.lower() in ("-command", "-c", "/c", "/k", "/r", "-com", "-comm")), None)
            if ci is not None:
                check_pwsh(pol, " ".join(ps_unquote(x) for x in rest[ci + 1:]), cwd, depth + 1)
            continue
        if verb in ("bash", "sh", "wsl") and depth < 3:
            ci = next((k for k, x in enumerate(rest) if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", x)), None)
            if ci is not None and ci + 1 < len(rest):
                check_shell(pol, ps_unquote(rest[ci + 1]), cwd, depth + 1)
            continue
        if verb in ("cd", "set-location", "sl", "chdir", "pushd", "push-location") and rest:
            got = ps_paths(cwd, rest[-1], env)
            cwd = got[0] if got else cwd
            continue
        for k, x in enumerate(rest):  # -OutFile of any command (Invoke-WebRequest …)
            if x.lower() == "-outfile" and k + 1 < len(rest):
                for p in ps_paths(cwd, rest[k + 1], env) or []:
                    pol.write(p)
        named, pos, switches, k = {}, [], set(), 0
        while k < len(rest):
            x = rest[k]
            xl = x.lower()
            if xl.startswith("-") and not re.fullmatch(r"-\d+", x):
                key = xl.split(":", 1)[0]
                if ":" in x:
                    named[key] = x.split(":", 1)[1]
                elif key in PS_VALUE and k + 1 < len(rest):
                    named[key] = rest[k + 1]
                    k += 1
                else:
                    switches.add(key)
            elif re.fullmatch(r"/[A-Za-z?]{1,8}(:\S*)?", x):  # cmd switches /q /s /MIR …
                switches.add(xl.split(":", 1)[0])
            else:
                pos += [y for y in re.split(r",(?=(?:[^'\"]*['\"][^'\"]*['\"])*[^'\"]*$)", x) if y]
            k += 1
        recursive = bool(PS_RECURSE & switches)
        if recursive and (verb in PS_LIST or verb in PS_DELETE):
            for t in ([named[p] for p in PS_PATH if p in named] + pos) or ["."]:
                base = ps_unquote(t).replace("\\", "/")
                base = os.path.dirname(base) or "." if any(ch in base for ch in "*?[") else base
                for p in ps_paths(cwd, base, env) or [cwd]:
                    p = pol.p(p)
                    if pol.is_raw(p) or pol.above_raw(p) or pol.in_raw(p):
                        over_raw.append(p)
            if verb == "forfiles" and over_raw and re.search(r"(?i)\b(del|erase|rd|rmdir|move|ren|rename|copy)\b", " ".join(rest)):
                any_source()
            if verb in PS_DELETE and over_raw:
                any_source()
        if verb not in PS_CHANGERS:
            continue
        unseen = names_raw or bool(over_raw)  # a target that can't be seen counts as raw/ when the command reaches it
        src_toks = [named[p] for p in PS_PATH if p in named] + pos[:1]
        if verb in PS_DELETE:
            targets = [named[p] for p in PS_PATH if p in named] + pos
            if not targets:
                if unseen:
                    any_source()
                continue
            for t in targets:
                got = ps_paths(cwd, t, env)
                if got is None:
                    if unseen:
                        any_source()
                    continue
                for p in got:
                    pol.delete(p)
        elif verb in PS_WRITE or verb in PS_APPEND:
            targets = src_toks
            if "-name" in named and verb in ("new-item", "ni"):
                targets = [os.path.join(ps_unquote(t), ps_unquote(named["-name"])) for t in (targets or ["."])]
            if not targets:
                if unseen:
                    any_source()
                continue
            for t in targets:
                got = ps_paths(cwd, t, env)
                if got is None:
                    if unseen:
                        any_source()
                    continue
                for p in got:
                    pol.write(p, append_only=verb in PS_APPEND or bool({"-a", "-append"} & switches))
        else:  # move, rename, copy: a source and a destination
            dst_tok = named.get("-destination") or named.get("-newname") or (pos[1] if len(pos) > 1 else None)
            if not src_toks:
                srcs = None
            else:
                srcs = ps_paths(cwd, src_toks[0], env)
            dst = ps_paths(cwd, dst_tok, env) if dst_tok else None
            if srcs is None or (dst_tok and dst is None):
                if unseen:
                    any_source()
                continue
            if verb == "robocopy":
                if dst and ({"/mir", "/purge"} & switches):
                    pol.delete(dst[0])
                if {"/mov", "/move"} & switches:
                    for sp in srcs:
                        pol.delete(sp)
                for sp in srcs:
                    if dst:
                        pol.copy_into(sp, dst[0], contents=True)
                continue
            if not dst:
                if any(pol.in_raw(pol.p(sp)) for sp in srcs) and verb not in PS_COPY:
                    any_source()
                continue
            for sp in srcs:
                if verb in PS_RENAME:
                    newname = ps_unquote(dst_tok).replace("\\", "/")
                    target = dst[0] if "/" in newname else os.path.join(os.path.dirname(sp), newname)
                    pol.move(sp, target)
                elif verb in PS_MOVE:
                    pol.move(sp, dst[0])
                else:
                    pol.copy_into(sp, dst[0], contents=(verb == "xcopy"))


def check_claude_event(pol, data, windows_shell=False):
    """windows_shell: the program's shell tool may run PowerShell or cmd on Windows (Codex, Qwen); Claude Code's Bash
    tool is Git Bash and has a PowerShell tool of its own."""
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
    elif name in ("Bash", "Monitor", "PowerShell"):
        cmd = ti.get("command")
        if isinstance(cmd, str):
            if name != "PowerShell":
                check_shell(pol, cmd, os.path.realpath(cwd))
            if name == "PowerShell":
                check_pwsh(pol, cmd, os.path.realpath(cwd))
            elif windows_shell and os.name == "nt":
                check_pwsh(pol, cmd, os.path.realpath(cwd), from_bash=True)


QWEN_NAMES = {"write_file": "Write", "edit": "Edit", "notebook_edit": "NotebookEdit", "run_shell_command": "Bash",
              "monitor": "Monitor"}


def qwen_path(p):
    """As Qwen's own tools read a path: trimmed, shell-escaped spaces unescaped."""
    return p.strip().replace("\\ ", " ") if isinstance(p, str) else p


def check_qwen_event(pol, data):
    """Qwen sends Claude's fields under its own tool names; a shell call may carry its working folder in `directory`."""
    name, ti = data.get("tool_name"), dict(data.get("tool_input") or {})
    mapped = QWEN_NAMES.get(name, name)
    cwd = data.get("cwd") or pol.root
    if mapped in ("Bash", "Monitor") and isinstance(ti.get("directory"), str) and ti["directory"].strip():
        cwd = os.path.join(cwd, os.path.expanduser(qwen_path(ti["directory"])))
    for k in ("file_path", "notebook_path"):
        if k in ti:
            ti[k] = qwen_path(ti[k])
    check_claude_event(pol, dict(data, tool_name=mapped, tool_input=ti, cwd=cwd), windows_shell=True)


FILE_OP = re.compile(r"^\s*\*\*\* (Add|Update|Delete) File: (.+?)\s*$")
MOVE_TO = re.compile(r"^\s*\*\*\* Move to: (.+?)\s*$")
PATCH_BODY = re.compile(r"\*\*\* Begin Patch.*?\*\*\* End Patch", re.S)
INVOKES_PATCH = re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:sudo\s+|env\s+)?(apply_patch|applypatch)\b", re.M)  # as a command word only


def apply_hunks(old, hunk):
    """Apply an Update body to old text; None when it does not apply cleanly."""
    text, chunks, cur = old, [], []
    for line in hunk:
        if line.startswith("@@"):
            if cur:
                chunks.append(cur)
            cur = []
        elif line[:1] in (" ", "-", "+"):
            cur.append(line)
        elif line == "":
            cur.append(" ")
    if cur:
        chunks.append(cur)
    for ch in chunks:
        before = "\n".join(x[1:] for x in ch if x[0] in " -")
        after = "\n".join(x[1:] for x in ch if x[0] in " +")
        if before == "":
            text = text + ("" if text.endswith("\n") or not text else "\n") + after + ("\n" if after else "")
            continue
        if text.count(before) != 1:
            return None
        text = text.replace(before, after, 1)
    return text


def check_patch(pol, body, cwd):
    lines = body.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = FILE_OP.match(lines[i])
        if not m:
            i += 1
            continue
        kind, path = m.group(1), os.path.join(cwd, m.group(2).strip())
        j, hunk, move = i + 1, [], None
        while j < len(lines) and not FILE_OP.match(lines[j]) and not lines[j].strip().startswith("*** End Patch"):
            mm = MOVE_TO.match(lines[j])
            if mm:
                move = os.path.join(cwd, mm.group(1).strip())
            else:
                hunk.append(lines[j])
            j += 1
        if kind == "Add":
            pol.write(path)
        elif kind == "Delete":
            pol.delete(path)
        else:
            if any(h[:1] in ("+", "-") for h in hunk):
                new, p = None, pol.p(path)
                if pol.in_notes(p) and os.path.isfile(p):
                    try:
                        with open(p, encoding="utf-8") as f:
                            new = apply_hunks(f.read(), hunk)
                    except Exception:
                        new = None
                if new is None and pol.source(p):
                    raise Denied(p, "edit")
                pol.write(path, new=new)
            if move:
                pol.move(path, move)
        i = j


def check_codex_event(pol, data):
    """Codex: file edits come as the apply_patch tool with the patch body; apply_patch can also be run from the shell
    (heredoc, quoted body, pipe, a patch file). Shell calls arrive as tool_name "Bash" with tool_input.command."""
    ti = data.get("tool_input") or {}
    cwd = os.path.realpath(data.get("cwd") or pol.root)
    if data.get("tool_name") == "apply_patch":
        body = ti.get("command") or ti.get("patch") or ti.get("input") or ""
        check_patch(pol, body if isinstance(body, str) else "\n".join(map(str, body)), cwd)
        return
    cmd = ti.get("command") if isinstance(ti.get("command"), str) else ""
    m = INVOKES_PATCH.search(cmd)
    if m:
        here = cwd  # `cd X && apply_patch …`: paths in the body are relative to X
        for cd in re.finditer(r"(?:^|[;&|(]\s*)cd\s+([^\s;&|)]+)", cmd[:m.start()]):
            here = os.path.realpath(os.path.join(here, os.path.expanduser(cd.group(1).strip("'\""))))
        bodies = PATCH_BODY.findall(cmd)
        for ref in re.findall(r"(?:\bcat\s+|<\s*)([^\s;&|<>]+)", cmd):
            try:
                with open(os.path.join(here, os.path.expanduser(ref.strip("'\""))), encoding="utf-8") as f:
                    bodies += PATCH_BODY.findall(f.read())
            except Exception:
                pass
        for b in bodies:
            check_patch(pol, b, here)
        if not bodies:  # the body is not visible: with sources in raw/, refuse rather than guess
            q = pol.sources_under(pol.raw)
            if q:
                raise Denied(q, "shell")
    check_claude_event(pol, dict(data, tool_name="Bash"), windows_shell=True)


def deny_message(pol, d):
    rel = os.path.relpath(d.path, pol.root)
    return ("raw/ guard: this call would " + {"edit": "change", "delete": "delete", "move": "move",
                                              "shell": "change or delete"}[d.why]
            + " " + rel + ", a source in raw/. The agent never edits, overwrites, renames, moves or deletes an existing raw/ "
            "file, even on the human's direct request (AGENTS.md, «`raw/` неизменяем» / \"`raw/` is immutable\"). "
            "If the call was aimed at raw/: do not retry it with another tool or command and do not ask the human to allow it "
            "in the program's permission prompt; tell the human that only they can change a source by hand, and offer to "
            "record their correction as knowledge from the conversation (a note in raw/из-чата/ or raw/from-chat/ and a wiki "
            "page citing it and the source). If the call was not aimed at raw/ (a broad path, pattern or project-wide edit "
            "caught it), narrow it so it leaves raw/ out. Allowed in raw/: new files; a new item at the end of the "
            "conversation note; moving a file (not a folder) from the raw/ root into input/ or a raw/ subfolder; renaming "
            "agent instruction files inside their folder; .gitkeep. Header and secret removal are done in input/ before "
            "placing (methodology/ingest.md, «Что можно в `raw/`» / \"What is allowed in `raw/`\").")


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common
    prog = common.program()
    data = common.read_event()
    common.mark_alive(prog, "guard")
    pol = Policy(common.ROOT)
    if not os.path.isdir(pol.raw):
        return
    try:
        {"claude": check_claude_event, "qwen": check_qwen_event, "codex": check_codex_event}[prog](pol, data)
    except Denied as d:
        common.deny(deny_message(pol, d))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass  # any failure of the guard lets the call through
