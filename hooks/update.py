"""Project update without git — the script branch of "Update check" in methodology/lint.md.

  check                       compare my version (line 3 of AGENTS.md) with the mirror; when the mirror is newer,
                              download both archives into tmp/update/ and say in tmp/update/report.md what
                              happens to each file
  apply <SHA> [--skip <path>] write what is safe, without network; <SHA> is the one check printed
  finish [--kept <path>]      raise line 3 to the mirror's version and remove tmp/update/ — after a full apply and
                              once every file left to the agent is handled (--kept: the human keeps it as it is)
  fetch <path|folder/> ...    files of my own version into tmp/update/files/mine-*.txt

Standard library only, Python 3.9+, identical in the RU and EN editions. Console output is ASCII; the last line is
`RESULT current|update|ok|failed:<reason>` (exit 0, 10, 0, 20) and, on failure, the line before it says what to do
next. No RESULT line — the script itself broke.
"""
import difflib, hashlib, io, json, os, re, shutil, ssl, subprocess, sys, urllib.error, urllib.request, zipfile

os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "tmp", "update")
URL = "https://github.com/DanilZherebtsov/"
RAW = "https://raw.githubusercontent.com/DanilZherebtsov/"
REPO = {"ru": "knowledge-base-constructor", "en": "knowledge-base-constructor-en"}
ABOUT = {"ru": "## О проекте", "en": "## About the project"}
MAIN = "AGENTS.md"
LINE3 = re.compile(r"^> \*\*(Версия|Version):\*\* (\d+)\.(\d+)\.(\d+)[ \t]*$")
KEEP_OUT = ("STATE.md", "BACKLOG.md", "wiki/", "raw/", "input/", "output/", "tmp/", "specs/")
GAP, TIMEOUT, BOM, CAFILE = 3, 30, b"\xef\xbb\xbf", "/etc/ssl/cert.pem"

NEXT = {
    "network": "no connection to github.com: see 'Network during update' in your environment file; "
               "this is not 'up to date'",
    "ssl": "certificate check failed and there is no curl: tell the human the connection to github.com failed; "
           "not 'up to date'",
    "proxy": "a proxy asks for a password: tell the human the update could not reach github.com",
    "notag": "the mirror has no tag of my version: tell the human plainly that the project cannot be updated "
             "without touching their edits, add the log line of step 2, stop",
    "ahead": "my version is higher than the mirror's: tell the human, change nothing",
    "lang": "the archive of my version is not in my language: stop, tell the human",
    "noversion": "line 3 of AGENTS.md has no version: check step 0 (an unfinished move); otherwise tell the human",
    "major": "the first digit changes: do not apply; tell the human the move needs git and a bash shell",
    "sha": "the mirror moved since check: run check again; if the report changed, ask the human again",
    "partial": "not everything was written: run check again, then apply with the SHA it prints; no finish",
    "notapplied": "apply has not completed: run check and apply first",
    "pending": "files left to the agent are not handled yet: handle each as the report says; for one the human keeps "
               "as it is, run finish --kept <path>",
    "nostate": "run check first",
    "line3": "line 3 is not the version check saw: run check again",
    "missing": "a path is not in my version: check the spelling",
    "usage": "commands: check | apply <SHA> [--skip <path>] | finish [--kept <path>] | fetch <path> ...",
    "internal": "the script broke: use the fallback in lint.md (version and changelog by the page-reading tool)",
}


class Fail(Exception):
    def __init__(self, reason, detail=""):
        Exception.__init__(self, reason)
        self.reason, self.detail = reason, detail


def say(text):
    sys.stdout.write(text + "\n")


# --- network (tests replace get) -------------------------------------------------------------------------------

def which(name):
    """A program by PATH only, never from the current folder (a planted curl.exe there must not run)."""
    exts = os.environ.get("PATHEXT", ".EXE").split(os.pathsep) if os.name == "nt" else [""]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.isabs(d):
            for e in exts:
                p = os.path.join(d, name + e)
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
    return None


def get(url):
    try:
        return urllib.request.urlopen(url, timeout=TIMEOUT).read()
    except urllib.error.HTTPError as e:
        raise Fail("proxy" if e.code == 407 else "network", "HTTP %d %s" % (e.code, url))
    except (OSError, ssl.SSLError) as e:  # URLError, timeouts and SSL errors are all OSError
        reason = getattr(e, "reason", e)
        if not (isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason)):
            raise Fail("network", "%s %s" % (reason, url))
    if os.path.isfile(CAFILE):  # python.org Python on a Mac has no certificate store of its own
        try:
            ctx = ssl.create_default_context(cafile=CAFILE)
            return urllib.request.urlopen(url, timeout=TIMEOUT, context=ctx).read()
        except Exception:
            pass
    curl = which("curl")
    if not curl:
        raise Fail("ssl", url)
    r = subprocess.run([curl, "-fsSL", "--max-time", str(2 * TIMEOUT), url], capture_output=True)
    if r.returncode:
        raise Fail("ssl", "curl %d %s" % (r.returncode, url))
    return r.stdout


def refs(repo):
    """SHA of HEAD and of every tag — the same source git ls-remote reads, without an API rate limit."""
    data = get(URL + repo + ".git/info/refs?service=git-upload-pack")
    out = {}
    for m in re.finditer(rb"([0-9a-f]{40}) ([^\s\x00]+)", data):
        name, sha = m.group(2).decode("ascii", "replace"), m.group(1).decode()
        if name.endswith("^{}"):
            out[name[:-3]] = sha
        else:
            out.setdefault(name, sha)
    if "HEAD" not in out:
        raise Fail("network", "no HEAD in info/refs")
    return out


def unzip(data, sha):
    """{path: bytes} without the archive's top folder; the zip comment must be the expected commit."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Fail("network", "not a zip archive")
    if z.comment.decode("ascii", "replace").strip() != sha:
        raise Fail("network", "the archive is not commit " + sha)
    return {i.filename.split("/", 1)[1]: z.read(i) for i in z.infolist()
            if not i.is_dir() and "/" in i.filename and i.filename.split("/", 1)[1]}


# --- text ----------------------------------------------------------------------------------------------------

def norm(b):
    if b is None:
        return None
    return (b[3:] if b.startswith(BOM) else b).replace(b"\r\n", b"\n")


def lines(b):
    return norm(b).decode("utf-8", "surrogateescape").split("\n")


def unlines(ls):
    return "\n".join(ls).encode("utf-8", "surrogateescape")


def version_of(b):
    """(lang, (x, y, z)) from line 3 of the main file, or None."""
    ls = lines(b) if b is not None else []
    m = LINE3.match(ls[2]) if len(ls) > 2 else None
    return ("ru" if m.group(1) == "Версия" else "en", tuple(int(x) for x in m.groups()[1:])) if m else None


def vstr(v):
    return "%d.%d.%d" % v


def cut(b, lang):
    """(core, line 3, "About the project" section): core has both replaced by marker lines; None if either is missing."""
    ls = lines(b)
    if len(ls) < 3 or not LINE3.match(ls[2]) or ABOUT[lang] not in ls:
        return None
    start = ls.index(ABOUT[lang])
    end = next((i for i in range(start + 1, len(ls)) if ls[i].startswith("## ")), len(ls))
    return ls[:2] + ["\x00line3"] + ls[3:start] + ["\x00about"] + ls[end:], ls[2], ls[start:end]


def uncut(core, line3, about):
    out = []
    for ln in core:
        out += [line3] if ln == "\x00line3" else about if ln == "\x00about" else [ln]
    return out


def hunks(a, b):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(i1, i2, tuple(b[j1:j2])) for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"]


def merge3(base, mine, new):
    """Cautious three-way merge of line lists. The same edit on both sides counts once (as in git merge-file);
    other edits are applied only when every pair is at least GAP unchanged base lines apart. Otherwise None."""
    hm, hn = hunks(base, mine), hunks(base, new)
    hn = [h for h in hn if h not in hm]
    for a1, a2, _ in hm:
        for b1, b2, _ in hn:
            if a1 - GAP < b2 and b1 - GAP < a2:
                return None
    out, pos = [], 0
    for i1, i2, rep in sorted(hm + hn, key=lambda h: (h[0], h[1])):
        out += base[pos:i1] + list(rep)
        pos = i2
    return out + base[pos:]


# --- project ---------------------------------------------------------------------------------------------------

def mine_bytes(path):
    try:
        with open(os.path.join(ROOT, path), "rb") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def mine_for(p, base):
    """My file for a path; a path renamed by letter case only is the same file under its old name."""
    m = mine_bytes(p)
    if m is None and p not in base:
        twin = next((q for q in base if q.lower() == p.lower()), None)
        m = mine_bytes(twin) if twin else None
    return m


def sha256(b):
    return None if b is None else hashlib.sha256(b).hexdigest()


def kept_out(path):
    return any(path == k or (k.endswith("/") and path.startswith(k)) for k in KEEP_OUT)


def ensure_work(*sub):
    os.makedirs(os.path.join(WORK, *sub), exist_ok=True)
    gi = os.path.join(WORK, ".gitignore")
    if not os.path.isfile(gi):
        with open(gi, "wb") as f:
            f.write(b"# Working files of the project update (hooks/update.py). Never commit.\n*\n")


def clear_work(keep_prev=False):
    """Remove tmp/update/; on a repeated check the bytes kept before a merge (prev/) stay until finish."""
    if not os.path.isdir(WORK):
        return
    for name in os.listdir(WORK):
        if keep_prev and name == "prev":
            continue
        full = os.path.join(WORK, name)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass
    if not keep_prev:
        shutil.rmtree(WORK, ignore_errors=True)


def put(path, data):
    """Whole-file write through a temporary name, so a cut-off run never leaves half a file."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    try:
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    except OSError:
        try:
            os.remove(path + ".part")
        except OSError:
            pass
        raise


def my_version():
    v = version_of(mine_bytes(MAIN))
    if not v:
        raise Fail("noversion")
    return v


def target(path, base, new, mine, lang):
    """The bytes apply writes for one changed path, or None when the agent has to merge it."""
    if path == MAIN:
        cm, cb, cn = cut(mine, lang), cut(base, lang), cut(new, lang)
        if not (cm and cb and cn):
            return None
        core = merge3(cb[0], cm[0], cn[0])
        return None if core is None else unlines(uncut(core, cm[1], cm[2]))
    if norm(mine) == norm(base):
        return new
    merged = merge3(lines(base), lines(mine), lines(new))
    return None if merged is None else unlines(merged)


def classify(base, new, lang):
    """[(path, outcome, bytes to write or None)] for every path the two archives differ in."""
    by_case = {p.lower(): p for p in base}
    out = []
    for p in sorted(set(base) | set(new)):
        b, n = base.get(p), new.get(p)
        if p not in base and p.lower() in by_case:  # renamed by letter case only: the same file
            b = base[by_case[p.lower()]]
        if norm(b) == norm(n) or kept_out(p):
            continue
        m = mine_for(p, base)
        if n is None and p.lower() in {q.lower() for q in new}:
            continue
        if "/" in p and p.startswith("."):
            out.append((p, "skip" if norm(m) == norm(n) else "agent-settings", None))
        elif n is None:
            out.append((p, "skip" if m is None else "agent-deleted-in-mirror", None))
        elif m is None:
            out.append((p, "write" if b is None else "agent-deleted-by-you", n if b is None else None))
        elif norm(m) == norm(n):
            out.append((p, "skip", None))
        elif b is None:
            out.append((p, "agent-new-differs", None))
        else:
            t = target(p, b, n, m, lang)
            if t is not None and norm(t) == norm(m):
                out.append((p, "skip", None))  # e.g. only line 3 differs: finish raises it
            elif t is None:
                out.append((p, "agent-both", None))
            else:
                whole = t is n or (p == MAIN and cut(m, lang)[0] == cut(b, lang)[0])
                out.append((p, "write" if whole else "write-merged", t))
    return out


def changelog(text, mine):
    """Entries from the newest down to the heading of my version, that heading included."""
    ls, head = text.split("\n"), re.compile(r"^## (\d+)\.(\d+)\.(\d+)\b")
    start = next((i for i, l in enumerate(ls) if head.match(l)), len(ls))
    for i in range(start, len(ls)):
        m = head.match(ls[i])
        if m and tuple(int(x) for x in m.groups()) == mine:
            return "\n".join(ls[start:i + 1])
    return "\n".join(ls[start:])


MEANING = """Outcomes:
- write: apply writes the mirror's file (yours was as in your version). write-merged: apply writes your file with
  the update merged in (your edits and the update's are far apart); your previous bytes go to tmp/update/prev/.
- skip: nothing to do.
- agent-both: you and the update changed the same place: files/NN-base.txt (your version) and NN-new.txt (the
  mirror) — carry the human's edit into the new file or ask, as in step 7.
- agent-new-differs: the update adds a file you already have with other content: show the difference, ask.
- agent-deleted-by-you: the update changed a file that is gone here: ask.
- agent-deleted-in-mirror: the update removed the file: say so, ask; do not delete it yourself.
- agent-settings: a file in a folder whose name starts with a dot: by records, step 7 and "Writing project checks
  and program settings"; apply never writes there.
finish refuses while an agent-* file is unchanged since check; one the human keeps as it is: finish --kept <path>.
"""


def cmd_check():
    lang, mine = my_version()
    r = refs(REPO[lang])
    sha = r["HEAD"]
    body = get(RAW + REPO[lang] + "/" + sha + "/versions.json")
    say("mirror sha: " + sha)
    say("versions.json (verbatim):")
    say(body.decode("utf-8", "replace").rstrip("\n"))
    try:
        mirror = tuple(int(x) for x in json.loads(body.decode("utf-8"))["version"].split("."))
    except (ValueError, KeyError, AttributeError):
        raise Fail("network", "versions.json has no version")
    say("mine: %s  mirror: %s" % (vstr(mine), vstr(mirror)))
    if mirror == mine:
        clear_work()
        return "current"
    if mine > mirror:
        raise Fail("ahead")
    tag = "refs/tags/v" + vstr(mine)
    if tag not in r:
        raise Fail("notag")
    bz, nz = get(URL + REPO[lang] + "/archive/" + tag + ".zip"), get(URL + REPO[lang] + "/archive/" + sha + ".zip")
    base, new = unzip(bz, r[tag]), unzip(nz, sha)
    if (version_of(base.get(MAIN)) or ("",))[0] != lang:
        raise Fail("lang")
    log = changelog(get(RAW + REPO[lang] + "/" + sha + "/CHANGELOG.md").decode("utf-8", "replace"), mine)
    clear_work(keep_prev=True)
    ensure_work("files")
    put(os.path.join(WORK, "base.zip"), bz)
    put(os.path.join(WORK, "new.zip"), nz)
    items = classify(base, new, lang)
    differ = sorted(p for p in set(base) | set(new) if base.get(p) != new.get(p))
    asks = re.search(r"Как принять|How to accept|How to adopt|^## .*\[(формат|format)\]", log, re.M)
    only_number = (differ == [MAIN] and mirror[0] == mine[0] and "refs/tags/v" + vstr(mirror) in r and not asks
                   and lines(base[MAIN])[:2] + lines(base[MAIN])[3:] == lines(new[MAIN])[:2] + lines(new[MAIN])[3:])
    rows, state, n = [], [], 0
    for p, outcome, data in items:
        num = ""
        if outcome.startswith("agent-"):
            n += 1
            num = "%02d" % n
            for side, d in (("base", base.get(p)), ("new", new.get(p))):
                if d is not None:
                    put(os.path.join(WORK, "files", "%s-%s.txt" % (num, side)), d)
        rows.append("| %s | `%s` | %s |" % (num, p, outcome))
        state.append({"path": p, "outcome": outcome, "mine": sha256(mine_for(p, base)), "target": sha256(data)})
    st = {"sha": sha, "tag_sha": r[tag], "mine": vstr(mine), "mirror": vstr(mirror), "lang": lang,
          "major": mirror[0] != mine[0], "only_number": only_number, "applied": False, "items": state}
    put(os.path.join(WORK, "state.json"), json.dumps(st, ensure_ascii=False, indent=1).encode("utf-8"))
    status = ["%s %s" % ("A" if p not in base else "D" if p not in new else "M", p) for p in differ]
    report = ["# Update report", "", "mirror sha: " + sha, "mine: %s  mirror: %s" % (vstr(mine), vstr(mirror)),
              "only-number: " + ("yes" if only_number else "no"), "major: " + ("yes" if st["major"] else "no"), "",
              "## Paths that differ between the archives of my version and the mirror", ""] + (status or ["(none)"]) + [
              "", "## Outcomes", "", "| # | path | outcome |", "|---|---|---|"] + rows + ["", MEANING,
              "## Changelog, verbatim, down to the heading of my version", "", log, "", "END OF REPORT", ""]
    put(os.path.join(WORK, "report.md"), BOM + "\n".join(report).encode("utf-8"))  # BOM: PowerShell 5.1 reads it as UTF-8
    say("only-number: %s  major: %s  apply writes: %d  for the agent: %d" % (
        "yes" if only_number else "no", "yes" if st["major"] else "no",
        sum(1 for _, o, _ in items if o.startswith("write")), n))
    say("report: tmp/update/report.md (UTF-8; in PowerShell 5.1 read it with -Encoding UTF8)")
    return "update"


def load_state():
    try:
        with open(os.path.join(WORK, "state.json"), "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        raise Fail("nostate")


def cmd_apply(args):
    if not args or len(args) % 2 == 0 or any(a != "--skip" for a in args[1::2]):
        raise Fail("usage")
    st = load_state()
    skip = {a.replace("\\", "/") for a in args[2::2]}
    known = {it["path"] for it in st["items"]}
    if skip - known:
        raise Fail("usage", "not in the report: " + ", ".join(sorted(skip - known)))
    if st["sha"] != args[0]:
        raise Fail("sha", "the report is for " + st["sha"])
    if st["major"]:
        raise Fail("major")
    with open(os.path.join(WORK, "base.zip"), "rb") as f:
        base = unzip(f.read(), st["tag_sha"])
    with open(os.path.join(WORK, "new.zip"), "rb") as f:
        new = unzip(f.read(), st["sha"])
    todo = [it for it in st["items"] if it["outcome"].startswith("write") and it["path"] not in skip]
    done, problems = 0, []
    for it in todo:
        p = it["path"]
        cur = mine_for(p, base)
        if sha256(cur) == it["target"]:
            done += 1  # already written by an earlier run
            continue
        if sha256(cur) != it["mine"]:
            problems.append("changed after check: " + p)
            continue
        b = base.get(p)
        if b is None and p not in base:
            b = next((base[q] for q in base if q.lower() == p.lower()), None)
        data = new[p] if cur is None else target(p, b, new[p], cur, st["lang"])
        if data is None or sha256(data) != it["target"]:
            problems.append("does not reproduce: " + p)
            continue
        try:
            if it["outcome"] == "write-merged" or p == MAIN:
                put(os.path.join(WORK, "prev", p + ".txt"), cur)  # .txt: never a rules file a program picks up
            put(os.path.join(ROOT, p), data)
            done += 1
        except OSError as e:
            problems.append("cannot write %s: %s" % (p, e.__class__.__name__))
    for line in problems:
        say(line)
    merged = [it["path"] for it in todo if it["outcome"] == "write-merged"]
    if merged:
        say("merged (the human's edits kept): " + ", ".join(merged))
    say("written %d of %d%s" % (done, len(todo), ", skipped on request: %d" % len(skip) if skip else ""))
    if problems:
        raise Fail("partial")
    st["applied"] = True
    put(os.path.join(WORK, "state.json"), json.dumps(st, ensure_ascii=False, indent=1).encode("utf-8"))
    return "ok"


def cmd_finish(args):
    if len(args) % 2 or any(a != "--kept" for a in args[::2]):
        raise Fail("usage")
    st = load_state()
    if not (st["applied"] or st["only_number"]):
        raise Fail("notapplied")
    kept = {a.replace("\\", "/") for a in args[1::2]}
    pending = [it["path"] for it in st["items"] if it["outcome"].startswith("agent-") and it["path"] not in kept
               and sha256(mine_bytes(it["path"])) == it["mine"]]
    if pending:
        raise Fail("pending", "not handled: " + ", ".join(pending))
    path = os.path.join(ROOT, MAIN)
    with open(path, "rb") as f:
        data = f.read()
    parts = data.split(b"\n")
    if version_of(data) != (st["lang"], tuple(int(x) for x in st["mine"].split("."))):
        raise Fail("line3")
    parts[2] = re.sub(rb"\d+\.\d+\.\d+", st["mirror"].encode(), parts[2], count=1)
    put(path, b"\n".join(parts))
    clear_work()
    say("line 3: %s -> %s" % (st["mine"], st["mirror"]))
    return "ok"


def cmd_fetch(paths):
    if not paths:
        raise Fail("usage")
    lang, mine = my_version()
    r = refs(REPO[lang])
    tag = "refs/tags/v" + vstr(mine)
    if tag not in r:
        raise Fail("notag")
    files = unzip(get(URL + REPO[lang] + "/archive/" + tag + ".zip"), r[tag])
    ensure_work("files")
    missing = 0
    for want in (p.replace("\\", "/") for p in paths):
        found = sorted(p for p in files if p == want or (want.endswith("/") and p.startswith(want)))
        if not found:
            say("not in my version: " + want)
            missing += 1
        for p in found:
            name = "mine-" + re.sub(r"[^A-Za-z0-9.-]", "_", p.replace("/", "__")).lstrip(".") + ".txt"
            put(os.path.join(WORK, "files", name), files[p])
            say("%s -> tmp/update/files/%s" % (p, name))
    if missing:
        raise Fail("missing")
    return "ok"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="ascii", errors="backslashreplace")
    except Exception:
        pass
    cmd, args = (argv[1] if len(argv) > 1 else ""), argv[2:]
    try:
        if cmd == "check" and not args:
            word = cmd_check()
        elif cmd == "apply":
            word = cmd_apply(args)
        elif cmd == "finish":
            word = cmd_finish(args)
        elif cmd == "fetch":
            word = cmd_fetch(args)
        else:
            raise Fail("usage")
    except Fail as e:
        word = "failed:" + e.reason
        if e.detail:
            say(e.detail)
        say("next: " + NEXT.get(e.reason, ""))
    except Exception as e:
        word = "failed:internal"
        say("error: %s: %s" % (e.__class__.__name__, e))
        say("next: " + NEXT["internal"])
    say("RESULT " + word)
    return {"current": 0, "ok": 0, "update": 10}.get(word, 20)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
