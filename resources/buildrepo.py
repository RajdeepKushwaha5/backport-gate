#!/usr/bin/env python3
"""Build a repository where ONE fix reaches four different verdicts.

Used twice, so the cases the self-check asserts are the shapes the demo shows: to create
the bundled demo repository at authoring time, and by the self-check at run time in a
throwaway directory it owns and removes.

The four release lines fork from one commit and then receive different amounts of what
came after it:

    release-1.x  nothing            -> the fix rewrites a line that is not there
    release-2.x  helper + refactor  -> applies, and everything it calls exists
    release-3.x  helper + refactor + the fix itself, under a different hash
    release-4.x  refactor only      -> applies cleanly and calls a helper that is absent

release-1.x is the trap: `git merge-tree <branch> <fix>` calls it clean, because a merge
brings the refactor along, and a real cherry-pick conflicts.
"""
import os
import subprocess

Q = chr(34)
NL = chr(10)

BASE_APP = (
    "def handle(req):" + NL +
    "    return req[" + Q + "body" + Q + "]" + NL)

REFACTORED_APP = (
    "def _extract(req):" + NL +
    "    return req[" + Q + "body" + Q + "]" + NL + NL +
    "def handle(req):" + NL +
    "    return _extract(req)" + NL)

# The fix both DEFINES a helper and CALLS it. That matters for the self-check: a symbol
# the patch brings with it must never be reported as missing from the branch, and
# without a fixture where the patch defines something, that guard cannot be tested.
FIXED_APP = (
    "from util import normalise_locale" + NL + NL +
    "def _blank_to_empty(value):" + NL +
    "    return value if value is not None else " + Q + Q + NL + NL +
    "def _extract(req):" + NL +
    "    return normalise_locale(_blank_to_empty(req.get(" + Q + "body" + Q + ")))" + NL + NL +
    "def handle(req):" + NL +
    "    return _extract(req)" + NL)

BASE_UTIL = "def tidy(s):" + NL + "    return s.strip()" + NL

HELPER_UTIL = (
    "def tidy(s):" + NL + "    return s.strip()" + NL + NL +
    "def normalise_locale(code):" + NL +
    "    return code.replace(" + Q + "_" + Q + ", " + Q + "-" + Q + ").lower()" + NL)


def run(args, cwd):
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
        "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    })
    p = subprocess.run(["git"] + args, cwd=cwd, env=e, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), p.stderr.strip()[:300]))
    return p.stdout


def write(root, rel, text):
    path = os.path.join(root, rel)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def commit(root, message):
    run(["add", "-A"], root)
    run(["commit", "-q", "-m", message], root)
    return run(["rev-parse", "HEAD"], root).strip()


def build(root):
    if not os.path.isdir(root):
        os.makedirs(root)
    run(["init", "-q", "-b", "main"], root)
    run(["config", "user.email", "demo@example.invalid"], root)
    run(["config", "user.name", "Demo"], root)
    run(["config", "commit.gpgsign", "false"], root)

    write(root, "app.py", BASE_APP)
    write(root, "util.py", BASE_UTIL)
    write(root, "README.md", "# demo repository for backport-gate" + NL)
    base = commit(root, "initial")

    for b in ("release-1.x", "release-2.x", "release-3.x", "release-4.x",
              "release-5.x", "feature-scratch"):
        run(["branch", b, base], root)

    # ---- main moves on
    write(root, "util.py", HELPER_UTIL)
    commit(root, "add normalise_locale helper")
    write(root, "app.py", REFACTORED_APP)
    commit(root, "refactor: extract _extract")

    # a merge commit, so a case can assert that cherry-picking one is refused
    run(["checkout", "-q", "-b", "sidework"], root)
    write(root, "NOTES.md", "side work" + NL)
    commit(root, "notes from side work")
    run(["checkout", "-q", "main"], root)
    run(["merge", "-q", "--no-ff", "-m", "merge side work", "sidework"], root)
    merge_sha = run(["rev-parse", "HEAD"], root).strip()

    # ---- THE FIX: edits inside the helper AND calls normalise_locale
    write(root, "app.py", FIXED_APP)
    fix = commit(root, "fix: empty body crashed the handler, and locale case leaked")

    # release-2.x took everything before the fix
    run(["checkout", "-q", "release-2.x"], root)
    write(root, "util.py", HELPER_UTIL)
    commit(root, "add normalise_locale helper")
    write(root, "app.py", REFACTORED_APP)
    commit(root, "refactor: extract _extract")

    # release-3.x took everything, and the fix as well, under its own hash
    run(["checkout", "-q", "release-3.x"], root)
    write(root, "util.py", HELPER_UTIL)
    commit(root, "add normalise_locale helper")
    write(root, "app.py", REFACTORED_APP)
    commit(root, "refactor: extract _extract")
    run(["cherry-pick", fix], root)
    picked = run(["rev-parse", "HEAD"], root).strip()

    # release-4.x took the refactor and NOT the helper: the patch will apply and the
    # branch will not work, which is the case a clean exit code hides
    run(["checkout", "-q", "release-4.x"], root)
    write(root, "app.py", REFACTORED_APP)
    commit(root, "refactor: extract _extract")

    # release-5.x dropped the file entirely, which git reports as modify/delete rather
    # than a content conflict. The distinction is the advice: there is nothing here to
    # merge by hand, the question is whether this fix belongs on that line at all.
    run(["checkout", "-q", "release-5.x"], root)
    os.remove(os.path.join(root, "app.py"))
    commit(root, "drop the request handler from this line")

    run(["checkout", "-q", "main"], root)
    run(["branch", "-D", "sidework"], root)
    return {"base": base, "fix": fix, "merge": merge_sha, "picked": picked}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(build(sys.argv[1]), indent=2))
