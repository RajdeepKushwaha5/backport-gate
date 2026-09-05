#!/usr/bin/env python3
"""Run the real analyzer against a repository built for the purpose, before it is
trusted with yours.

The assertions that prove an analyzer works normally live on the author's machine, where
nobody running the play can see them. This builds a throwaway repository under the system
temp directory on every run, feeds it to the shipped backport.py, and the presentation
withholds its verdict if any case fails. Nothing is ever written to your repository.

Four things a self-check of this play has to assert, each because leaving it out lets a
wrong answer ship behind a green count:

  EVERY VERDICT NEEDS A POSITIVE CASE. A rule with only negative cases can be deleted and
  the check still passes.

  DISCOVERY IS CHECKED. A run that finds no release branches reports nothing to do, and
  every classification case would still pass on an empty list.

  THE PRIMITIVE IS PINNED. `git merge-tree <branch> <fix>` is a merge and answers a
  different question than a cherry-pick. It is run here on purpose, and the case fails
  unless it disagrees with the analyzer: that disagreement is the whole reason
  --merge-base is in the command.

  A FAILED TOOL IS NOT AN ANSWER. git that cannot run must not come back as a clean
  report about the repository.

    selfcheck.py  ->  JSON {passed, total, failures}
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZER = os.path.join(HERE, "backport.py")

# branch -> the verdict the built repository must produce for the fix
EXPECTED = {
    "release-1.x": "CONFLICTS",
    "release-2.x": "APPLIES_CLEAN",
    "release-3.x": "ALREADY_PRESENT",
    "release-4.x": "CLEAN_BUT_UNDERPINNED",
    "release-5.x": "CONFLICTS",
}
MUST_COVER = {"CONFLICTS", "APPLIES_CLEAN", "ALREADY_PRESENT",
              "CLEAN_BUT_UNDERPINNED", "NOT_APPLICABLE"}


def run_json(args):
    # spawned as a literal so the command can be checked against deps.toml
    p = subprocess.run(["python3"] + args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip()[:200] or "exit %d" % p.returncode)
    return json.loads(p.stdout)


def analyse(root, fix, branches=""):
    plan = run_json([ANALYZER, "plan", root, fix, branches])
    if plan.get("status") != "ok":
        return plan, None
    out = run_json([ANALYZER, "rehearse", root, json.dumps(plan)])
    return plan, out


def git(args, root):
    return subprocess.run(["git", "-C", root] + args, capture_output=True, text=True)


def main():
    failures, total = [], 0
    seen = set()
    scratch = None

    try:
        sys.path.insert(0, HERE)
        import buildrepo
        scratch = tempfile.mkdtemp(prefix="backport-gate-selfcheck-")
        repo = os.path.join(scratch, "repo")
        shas = buildrepo.build(repo)
    except Exception as e:
        print(json.dumps({"passed": 0, "total": 1, "failures": [
            {"case": "fixture-builds", "detail": str(e)[:200]}]},
            separators=(",", ":")))
        return

    try:
        plan, out = analyse(repo, shas["fix"])
        got = {}
        if out is not None:
            got = {r["branch"]: r for r in out.get("results", [])}

        # ---- one positive case per verdict, from one commit
        for branch, verdict in sorted(EXPECTED.items()):
            total += 1
            seen.add(verdict)
            row = got.get(branch)
            if row is None:
                failures.append({"case": "%s" % branch,
                                 "detail": "expected %s, no result was produced" % verdict})
            elif row["verdict"] != verdict:
                failures.append({"case": "%s" % branch,
                                 "detail": "expected %s, produced %s"
                                           % (verdict, row["verdict"])})

        # the underpinned branch must name the symbol, not merely flag the branch
        total += 1
        missing = (got.get("release-4.x") or {}).get("missing_symbols") or []
        if "normalise_locale" not in missing:
            failures.append({
                "case": "underpinned:names-the-symbol",
                "detail": "the branch that applies cleanly and then breaks did not name "
                          "normalise_locale, it named %r" % missing})

        # a symbol the patch itself defines travels with the patch and is not missing
        total += 1
        clean_missing = (got.get("release-2.x") or {}).get("missing_symbols") or []
        if clean_missing:
            failures.append({
                "case": "underpinned:no-false-positive",
                "detail": "a branch with everything the fix needs reported %r missing"
                          % clean_missing})

        # the KIND of conflict is the advice, so it has to survive into the row: a file
        # deleted on the branch is not a hand-merge, it is a question about scope
        total += 1
        kinds5 = (got.get("release-5.x") or {}).get("conflict_kinds") or []
        kinds1 = (got.get("release-1.x") or {}).get("conflict_kinds") or []
        if "modify/delete" not in kinds5:
            failures.append({
                "case": "conflict:kind-is-reported",
                "detail": "a branch that deleted the file reported kinds %r; reporting "
                          "that as an ordinary conflict gives the wrong advice" % kinds5})
        total += 1
        if kinds1 == kinds5:
            failures.append({
                "case": "conflict:kinds-are-distinguished",
                "detail": "a content conflict and a modify/delete conflict came back "
                          "identical (%r), so the distinction is not being read" % kinds1})

        # ---- a merge commit cannot be rehearsed, and says so
        total += 1
        _, mout = analyse(repo, shas["merge"], "release-1.x")
        mv = ((mout or {}).get("results") or [{}])[0].get("verdict")
        if mv != "NOT_APPLICABLE":
            failures.append({"case": "merge-commit:refused",
                             "detail": "cherry-picking a merge commit needs a parent "
                                       "chosen by hand; got %s" % mv})
        else:
            seen.add("NOT_APPLICABLE")

        # ---- discovery
        total += 1
        found = (plan or {}).get("branches") or []
        if len(found) != 5:
            failures.append({"case": "discovery:finds-the-release-lines",
                             "detail": "expected 5 release branches, found %r" % found})
        total += 1
        skipped = [s["name"] for s in (plan or {}).get("skipped", [])]
        if "feature-scratch" not in skipped:
            failures.append({"case": "discovery:skips-what-is-not-a-release-line",
                             "detail": "a scratch branch was treated as a release line; "
                                       "skipped=%r" % skipped})

        # ---- THE PRIMITIVE. This is the case the play exists because of.
        #
        # Without --merge-base, merge-tree merges the branch with the fix and brings
        # every commit in between along, so release-1.x comes back clean. With it, the
        # command has cherry-pick semantics and conflicts, which is what an actual
        # cherry-pick does. If these two ever agree, the fixture has stopped exercising
        # the trap and the case is worthless, so agreement is a failure.
        total += 1
        wrong = git(["merge-tree", "--write-tree", "--name-only",
                     "release-1.x", shas["fix"]], repo)
        right = git(["merge-tree", "--write-tree", "--name-only",
                     "--merge-base=" + shas["fix"] + "^", "release-1.x", shas["fix"]], repo)
        if not (wrong.returncode == 0 and right.returncode == 1):
            failures.append({
                "case": "primitive:merge-base-is-load-bearing",
                "detail": "expected the merge form to report clean (0) and the "
                          "cherry-pick form to conflict (1); got %s and %s. Without that "
                          "difference this fixture no longer proves --merge-base matters"
                          % (wrong.returncode, right.returncode)})
        total += 1
        if (got.get("release-1.x") or {}).get("verdict") != "CONFLICTS":
            failures.append({
                "case": "primitive:analyzer-uses-the-right-one",
                "detail": "git's own cherry-pick rehearsal conflicts on release-1.x but "
                          "the analyzer did not report CONFLICTS, so it is asking the "
                          "merge question instead"})

        # ---- a failed tool is not an answer about the repository
        total += 1
        empty = os.path.join(scratch, "not-a-repo")
        os.makedirs(empty)
        nplan = run_json([ANALYZER, "plan", empty, "HEAD", ""])
        if nplan.get("status") != "not-a-git-repo":
            failures.append({"case": "git:not-a-repo-is-reported",
                             "detail": "a directory outside git returned %r"
                                       % nplan.get("status")})
        total += 1
        stub = os.path.join(scratch, "stubbin")
        os.makedirs(stub)
        with open(os.path.join(stub, "git"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh" + chr(10) + "exit 1" + chr(10))
        os.chmod(os.path.join(stub, "git"), 0o755)
        env = dict(os.environ)
        env["PATH"] = stub + os.pathsep + env.get("PATH", "")
        p = subprocess.run(["python3", ANALYZER, "plan", repo, "HEAD", ""],
                           capture_output=True, text=True, env=env)
        try:
            broken = json.loads(p.stdout).get("status")
        except ValueError:
            broken = "(no json)"
        if broken != "git-unavailable":
            failures.append({
                "case": "git:failure-is-not-a-clean-report",
                "detail": "with git failing the plan reported %r. A tool that could not "
                          "run is not an answer about the repository, and reporting "
                          "anything else here tells the reader nothing is wrong when "
                          "nothing was checked" % broken})

        # ---- refuse a relative root rather than resolving it against the wrong tree
        total += 1
        rel = subprocess.run(["python3", ANALYZER, "plan", "some/relative/path", "HEAD", ""],
                             capture_output=True, text=True)
        if rel.returncode == 0 or "ABSOLUTE" not in (rel.stderr or ""):
            failures.append({"case": "root:relative-path-refused",
                             "detail": "a relative root was not refused; a step runs "
                                       "inside rote's workspace, so it would have "
                                       "examined the wrong repository"})
    finally:
        if scratch and os.path.isdir(scratch):
            shutil.rmtree(scratch, ignore_errors=True)

    for verdict in sorted(MUST_COVER - seen):
        total += 1
        failures.append({"case": "coverage:%s" % verdict,
                         "detail": "no bundled case asserts this verdict, so removing the "
                                   "rule that produces it would not be noticed"})

    print(json.dumps({"passed": total - len(failures), "total": total,
                      "failures": failures[:10]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
