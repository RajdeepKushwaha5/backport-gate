#!/usr/bin/env python3
"""backport-gate: can this fix be carried onto the release branches you still support?

    backport.py plan     <root> <fix> [branches]
    backport.py rehearse <root> <plan-json>

Answers one question per maintained branch, from local git alone, and never writes:
would cherry-picking THIS commit onto THAT branch apply, and if it applies, is it
actually underpinned there.

THE TRAP THIS PLAY EXISTS BECAUSE OF. The obvious way to ask git whether a fix will
land is `git merge-tree <branch> <fix>`, and it answers a DIFFERENT QUESTION. That is
a merge: it brings every commit between the fork point and the fix along with it, so a
fix that depends on a refactor the branch never received comes back clean. Measured on
a repository built for this file:

    git merge-tree --write-tree --name-only release-1.x <fix>        -> exit 0, CLEAN
    git merge-tree --write-tree --name-only --merge-base=<fix>^ ...  -> exit 1, CONFLICT
    an actual `git cherry-pick <fix>` in a throwaway worktree        -> CONFLICT

Only the second form is cherry-pick semantics, and it agrees with the real cherry-pick.
The first form is what a naive implementation reaches for, and it would have told you a
broken backport was safe. `--merge-base` is not optional decoration here; it is the
whole difference between an answer and a wrong answer, and a bundled case pins it.

A clean application is still not behavioural safety, and this never claims otherwise.
It reports what git can prove and names what it cannot.

Read-only by construction: every git call passes an allow-list of query subcommands, so
cherry-pick, checkout, commit, merge, rebase, reset, push, fetch and worktree cannot be
spelled through it at all. Nothing is ever written, fetched or checked out.
"""
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True

# Query subcommands only. A writing verb is not merely avoided, it is unspellable.
ALLOWED = frozenset([
    "rev-parse", "rev-list", "log", "show", "cherry", "merge-base", "merge-tree",
    "for-each-ref", "cat-file", "grep", "diff-tree", "symbolic-ref",
])

MAX_BRANCHES = 40      # branches rehearsed; more are reported as not examined
MAX_SYMBOLS = 25       # identifiers checked for underpinning, per branch
MAX_CONFLICT_FILES = 20
MAX_ADDED_LINES = 4000

# Branch names people actually maintain releases on. Anything the caller names
# explicitly is used as given; this pattern is only for discovery.
RELEASE_SHAPED = re.compile(
    "^(release|releases|rel|stable|maint|maintenance|support|lts|hotfix)"
    "([/_-].*)?$|^v?[0-9]+[.][0-9]+([.](x|[0-9]+))?$|^[0-9]+[.]x$",
    re.I)

# An identifier followed by an opening parenthesis is a call. Built with re.escape so
# this file contains no backslash escape of its own: a flattened escape has silently
# broken a regex on this project before, and the failure is invisible in the source.
CALL = re.compile("([A-Za-z_][A-Za-z0-9_]{2,})[ ]*" + re.escape("("))

# A definition introduced by the patch itself. Without this the play reports a symbol as
# missing from the branch when the patch is the thing that brings it: the first run of
# this analyzer called `pick` an absent helper on all four branches, and `pick` was
# defined in a file the same commit adds.
DEFINES = re.compile(
    "(?:def|class|function|func)[ ]+([A-Za-z_][A-Za-z0-9_]*)"
    "|(?:const|let|var)[ ]+([A-Za-z_][A-Za-z0-9_]*)[ ]*=")

# Names that are calls but never a symbol a branch could be missing.
NOISE = frozenset("""
if elif while for return yield assert print len range str int float bool list dict set
tuple type isinstance hasattr getattr setattr open sorted enumerate zip map filter sum
min max abs round any all repr format join split strip append extend items keys values
get put post delete self super new this function class def const let var return await
async catch throw try except finally with lambda import from raise not and or in is
""".split())

GIT_OK, GIT_NO, GIT_DOWN, GIT_EXIT = "ok", "no", "down", "exit"


def git_run(args, root, timeout=30):
    """Run one read-only git query and report which of four things happened.

    A tool that could not run is not an answer about the repository. Folding a missing
    git, a timeout and a real non-zero exit into one None is how a broken machine gets
    reported as a clean result, so the outcome is carried explicitly.
    """
    if not args or args[0] not in ALLOWED:
        raise ValueError("refused non-query git subcommand: %r" % (args[0] if args else None,))
    try:
        p = subprocess.run(["git", "-C", root] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return {"rc": None, "out": "", "err": "", "how": GIT_DOWN}
    if p.returncode == 0:
        return {"rc": 0, "out": p.stdout, "err": p.stderr, "how": GIT_OK}
    if "not a git repository" in (p.stderr or "").lower():
        return {"rc": p.returncode, "out": p.stdout, "err": p.stderr, "how": GIT_NO}
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr, "how": GIT_EXIT}


def git_out(args, root, timeout=30):
    r = git_run(args, root, timeout)
    return r["out"] if r["how"] == GIT_OK else None


def resolve_root(value):
    if value != "demo":
        return value
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "repo")


def require_absolute(raw):
    if raw != "demo" and not os.path.isabs(raw):
        sys.stderr.write(
            "root must be an ABSOLUTE path, or the word demo. Got: " + raw + chr(10) +
            "A step runs inside rote's own workspace, not the directory you were "
            "standing in, so a relative path silently examines the wrong repository. "
            "There is no correct fallback: the step cannot see your shell directory."
            + chr(10))
        sys.exit(2)


# --------------------------------------------------------------------------- plan

def discover_branches(root):
    """Local branches that look like maintained release lines.

    Remote-tracking refs are deliberately not swept: a fork's mirror of somebody else's
    release branches is not something you can carry a fix onto, and counting them would
    inflate every number here.
    """
    out = git_out(["for-each-ref", "--format=%(refname:short)", "refs/heads"], root)
    if out is None:
        return [], []
    names = [n.strip() for n in out.splitlines() if n.strip()]
    picked = [n for n in names if RELEASE_SHAPED.match(n)]
    skipped = [{"name": n, "reason": "does not look like a release line"}
               for n in names if n not in picked]
    return picked, skipped


def commit_facts(root, ref):
    out = git_out(["rev-parse", "--verify", ref + "^{commit}"], root)
    if not out:
        return None
    sha = out.strip()
    meta = git_out(["log", "-1", "--format=%H%n%h%n%s%n%P", sha], root)
    if not meta:
        return None
    lines = meta.split(chr(10))
    parents = (lines[3].split() if len(lines) > 3 else [])
    files = git_out(["diff-tree", "--no-commit-id", "--name-only", "-r",
                     "--no-renames", sha], root) or ""
    return {
        "sha": lines[0], "short": lines[1], "subject": lines[2],
        "parents": parents, "is_merge": len(parents) > 1, "is_root": len(parents) == 0,
        "files": [f for f in files.splitlines() if f][:200],
    }


def plan(root, fix_ref, branch_arg):
    probe = git_run(["rev-parse", "--git-dir"], root)
    # Only git SAYING "not a git repository" is an answer about repositories. A missing
    # binary, a timeout, or a non-zero exit git did not explain is this play failing to
    # look, and reporting that as "not a git repository" tells the reader something
    # false about their machine. Written this way because the first version of this
    # function folded GIT_EXIT in with GIT_NO and the bundled case caught it.
    if probe["how"] in (GIT_DOWN, GIT_EXIT):
        return {"status": "git-unavailable",
                "detail": "git could not be run here, so nothing was examined. This is "
                          "not a statement about the repository."}
    if probe["how"] == GIT_NO:
        return {"status": "not-a-git-repo", "detail": root}

    fix = commit_facts(root, fix_ref)
    if fix is None:
        return {"status": "no-such-commit", "detail": fix_ref}
    if fix["is_root"]:
        return {"status": "fix-is-root-commit",
                "detail": "a root commit has no parent, so there is no patch to carry"}

    if branch_arg.strip():
        named = [b.strip() for b in branch_arg.split(",") if b.strip()]
        branches, skipped = [], []
        for b in named:
            if git_out(["rev-parse", "--verify", b + "^{commit}"], root):
                branches.append(b)
            else:
                skipped.append({"name": b, "reason": "no such branch in this repository"})
        discovery = "named by you"
    else:
        branches, skipped = discover_branches(root)
        discovery = "discovered by branch name"

    if not branches:
        return {"status": "no-branches",
                "detail": "no maintained release branch was found or named",
                "skipped": skipped[:20], "discovery": discovery}

    over = max(0, len(branches) - MAX_BRANCHES)
    label = ("demo (bundled repository)"
             if root.endswith(os.path.join("demo", "repo")) else root)
    return {
        "status": "ok", "root": root, "root_label": label,
        "discovery": discovery, "fix": fix,
        "branches": branches[:MAX_BRANCHES],
        "branches_not_examined": over,
        "skipped": skipped[:20],
    }


# ----------------------------------------------------------------------- rehearse

def parse_merge_tree(out):
    """merge-tree prints the tree oid, then the conflicted paths, then its messages.

    With --name-only the middle section is bare paths, so the blank line is the only
    boundary. A tree oid with nothing after it is a clean result.
    """
    lines = out.split(chr(10))
    files, messages, seen_blank = [], [], False
    for ln in lines[1:]:
        if not ln.strip():
            seen_blank = True
            continue
        (messages if seen_blank else files).append(ln.strip())
    return files, messages


def added_identifiers(root, fix):
    """What the fix's added lines CALL, and what those same lines DEFINE.

    Only added lines matter: a symbol the fix stops using cannot be missing anywhere.
    Both halves are needed, because a patch that adds a helper and calls it in the same
    commit is self-sufficient, and reporting that helper as absent from the branch is a
    finding about the patch rather than about the branch.
    """
    out = git_out(["show", "-U0", "--format=", "--no-renames", fix["sha"]], root, 60)
    if not out:
        return [], set()
    names, seen, defined = [], set(), set()
    for i, ln in enumerate(out.split(chr(10))):
        if i > MAX_ADDED_LINES:
            break
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        body = ln[1:]
        for m in DEFINES.finditer(body):
            defined.add(m.group(1) or m.group(2))
        for m in CALL.finditer(body):
            n = m.group(1)
            if n in NOISE or n in seen:
                continue
            seen.add(n)
            names.append(n)
    return names, defined


def defines_pattern(name):
    # POSIX ERE, and no backslash anywhere: [(:] instead of an escaped parenthesis.
    return ("(^|[^A-Za-z0-9_])(def|class|function|func)[[:space:]]+" + name +
            "[[:space:]]*[(:]|(^|[^A-Za-z0-9_])(const|let|var)[[:space:]]+" + name +
            "[[:space:]]*=")


def defined_at(root, ref, name):
    """Is this symbol DEFINED anywhere in the tree at ref?

    Deliberately narrow: Python, JavaScript/TypeScript and Go definition forms only.
    A language whose definitions this cannot recognise produces no finding rather than
    a guess, and the report says so.
    """
    r = git_run(["grep", "-I", "-q", "-E", "-e", defines_pattern(name), ref], root, 30)
    if r["how"] == GIT_OK:
        return True
    if r["how"] == GIT_EXIT and r["rc"] == 1:
        return False
    return None          # git could not answer; never guessed


def underpinning(root, fix, branch):
    """A clean cherry-pick can still be wrong.

    If the fix's added lines call something that exists on the fix's own side and does
    not exist on the target branch, the patch will apply and the branch will not work.
    git says clean because the text merged; nothing in the text says the helper is gone.
    """
    missing, unchecked = [], 0
    names, brought = added_identifiers(root, fix)
    for name in names[:MAX_SYMBOLS]:
        if name in brought:
            continue                       # the patch defines it; it travels with it
        here = defined_at(root, fix["sha"], name)
        if here is not True:
            if here is None:
                unchecked += 1
            continue                       # not defined on our side either: not ours
        there = defined_at(root, branch, name)
        if there is None:
            unchecked += 1
        elif there is False:
            missing.append(name)
    return missing, unchecked, len(names), max(0, len(names) - MAX_SYMBOLS)


def judge_branch(root, fix, branch):
    row = {"branch": branch, "conflict_files": [], "missing_symbols": [],
           "symbols_unchecked": 0, "symbols_seen": 0, "symbols_over_cap": 0}

    # 1. the commit itself is already in this branch's history
    anc = git_run(["merge-base", "--is-ancestor", fix["sha"], branch], root)
    if anc["how"] == GIT_DOWN:
        row["verdict"] = "UNCHECKED"
        row["detail"] = "git could not be run for this branch"
        return row
    if anc["how"] == GIT_OK:
        row["verdict"] = "ALREADY_PRESENT"
        row["detail"] = "this exact commit is already an ancestor of the branch"
        return row

    # 2. an equivalent patch landed under a different hash, which is what a cherry-pick
    #    or a rebase produces. `git cherry` compares patch ids, the technique
    #    lgoyal6/branch-debt uses at much greater depth for branch lifecycle.
    ch = git_run(["cherry", branch, fix["sha"], fix["sha"] + "^"], root)
    if ch["how"] == GIT_OK and ch["out"].strip().startswith("-"):
        row["verdict"] = "ALREADY_PRESENT"
        row["detail"] = ("an equivalent patch is already on the branch under a different "
                         "hash, by patch id")
        return row

    if fix["is_merge"]:
        row["verdict"] = "NOT_APPLICABLE"
        row["detail"] = ("this is a merge commit; a cherry-pick of it needs a parent "
                         "chosen by hand, which this play will not guess")
        return row

    # 3. cherry-pick rehearsal. --merge-base is the whole point: without it this is a
    #    merge and answers a different question. See the module docstring.
    mt = git_run(["merge-tree", "--write-tree", "--name-only",
                  "--merge-base=" + fix["sha"] + "^", branch, fix["sha"]], root, 60)
    if mt["how"] == GIT_DOWN or mt["rc"] is None or (mt["rc"] or 0) > 1:
        row["verdict"] = "UNCHECKED"
        row["detail"] = ("git could not rehearse this one: " +
                         (mt["err"] or "").strip()[:160])
        return row

    if mt["rc"] == 1:
        files, messages = parse_merge_tree(mt["out"])
        row["verdict"] = "CONFLICTS"
        row["conflict_files"] = files[:MAX_CONFLICT_FILES]
        row["conflict_files_total"] = len(files)
        # The KIND matters more than the count. A modify/delete conflict means the file
        # is not on that branch at all, so the question is whether this fix belongs there
        # in any form; a content conflict is an ordinary hand-merge. Reporting both as
        # "conflicts in 1 file" gives the same advice for two different problems.
        kinds = sorted(set(
            m.split(")")[0].split("(")[1].strip()
            for m in messages
            if m.upper().startswith("CONFLICT (") and ")" in m))
        row["conflict_kinds"] = kinds
        row["messages"] = [m for m in messages if m.upper().startswith("CONFLICT")][:6]
        shape = (" (%s)" % ", ".join(kinds)) if kinds else ""
        row["detail"] = ("cherry-picking this commit onto the branch conflicts in %d "
                         "file(s)%s" % (len(files), shape))
        return row

    missing, unchecked, seen, over = underpinning(root, fix, branch)
    row["symbols_seen"] = seen
    row["symbols_unchecked"] = unchecked
    row["symbols_over_cap"] = over
    if missing:
        row["verdict"] = "CLEAN_BUT_UNDERPINNED"
        row["missing_symbols"] = missing[:12]
        row["detail"] = ("the patch applies, but it calls %s, which is defined on the "
                         "fix's side and nowhere on this branch"
                         % ", ".join(missing[:3]))
        return row

    row["verdict"] = "APPLIES_CLEAN"
    row["detail"] = "the patch applies with no conflict"
    return row


def rehearse(data):
    fix = data["fix"]
    root = data["root"]
    results = [judge_branch(root, fix, b) for b in data.get("branches", [])]
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "status": "ok",
        "root_label": data.get("root_label", root),
        "discovery": data.get("discovery", ""),
        "fix": fix,
        "results": results,
        "counts": counts,
        "branches_not_examined": data.get("branches_not_examined", 0),
        "skipped": data.get("skipped", []),
    }


ORDER = ["CONFLICTS", "CLEAN_BUT_UNDERPINNED", "APPLIES_CLEAN", "ALREADY_PRESENT",
         "NOT_APPLICABLE", "UNCHECKED"]


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: backport.py plan|rehearse <root> [args]" + chr(10))
        sys.exit(2)
    mode, raw = sys.argv[1], sys.argv[2]
    require_absolute(raw)
    root = resolve_root(raw)
    if not os.path.isdir(root):
        sys.stderr.write("error: no such directory: " + root + chr(10))
        sys.exit(2)

    if mode == "plan":
        fix_ref = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "HEAD"
        branch_arg = sys.argv[4] if len(sys.argv) > 4 else ""
        print(json.dumps(plan(root, fix_ref, branch_arg), separators=(",", ":")))
    elif mode == "rehearse":
        if len(sys.argv) < 4:
            sys.stderr.write("error: rehearse needs the plan json" + chr(10))
            sys.exit(2)
        data = json.loads(sys.argv[3])
        if data.get("status") != "ok":
            print(json.dumps(data, separators=(",", ":")))
            return
        data["root"] = root
        print(json.dumps(rehearse(data), separators=(",", ":")))
    else:
        sys.stderr.write("unknown mode: " + mode + chr(10))
        sys.exit(2)


if __name__ == "__main__":
    main()
