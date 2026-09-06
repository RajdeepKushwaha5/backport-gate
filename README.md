# backport-gate

**You fixed a bug on main. You still support release-1.x. Can the fix go there?**

```bash
rote play run https://play.modiqo.ai/rajdeepkushwaha/backport-gate
```

That runs a bundled repository with nothing set up. For your own, pass an absolute
`root=` and the commit: `root=/path/to/repo fix=abc1234`.

Read-only. It never checks out, cherry-picks, commits or writes anything.

## Five answers

| verdict | what is being claimed |
|---|---|
| `CONFLICTS` | Cherry-picking would stop and ask you to resolve something. The files are named, **and so is the kind** — a `modify/delete` means the file is not on that branch at all, which is a question about scope, not a hand-merge. |
| `CLEAN_BUT_UNDERPINNED` | **The one worth having.** The patch applies with no conflict and the branch would still be broken, because the fix calls something that exists on its own side and nowhere on that branch. |
| `APPLIES_CLEAN` | It applies, and everything its new lines call exists there. Not a claim that the branch still behaves correctly. |
| `ALREADY_PRESENT` | It is there already — either this commit is an ancestor, or an equivalent patch landed under a different hash and was matched by patch id. |
| `NOT_APPLICABLE` | Nothing was rehearsed, and why. A merge commit needs a parent chosen by hand, and this will not choose one for you. |

## The trap this exists because of

The obvious way to ask git whether a fix will land is `git merge-tree <branch> <fix>`.
It answers a **different question**. That is a *merge*: it brings every commit between
the fork point and the fix along with it, so a fix that depends on a refactor the branch
never received comes back clean.

Measured on the bundled repository:

```
git merge-tree --write-tree --name-only release-1.x <fix>          -> exit 0, CLEAN
git merge-tree --write-tree --name-only --merge-base=<fix>^ ...    -> exit 1, CONFLICT
an actual `git cherry-pick <fix>` in a throwaway worktree          -> CONFLICT
```

Only the second form has cherry-pick semantics, and it is the one that agrees with the
real cherry-pick. `--merge-base` is the whole difference between an answer and a wrong
answer, so a bundled case runs **both** forms and fails unless they disagree.

## A clean apply is not a safe apply

`release-4.x` in the bundled repository took the refactor and not the helper. The patch
applies with no conflict. Then:

```
$ git cherry-pick <fix>          # succeeds
$ python -c "import app"
ImportError: cannot import name 'normalise_locale' from 'util'
```

That is what `CLEAN_BUT_UNDERPINNED` is for. The check is narrow on purpose: it compares
the identifiers the fix's **added lines call** against definitions present on the branch,
and it excludes anything the patch defines itself — the first version of this analyzer
reported a helper as missing from four branches when the same commit was the thing that
added it.

## Verified against ground truth, not against itself

Every verdict was checked by actually performing the cherry-pick in a throwaway worktree
and, where it applied, running the code:

```
release-1.x  CONFLICT (content): Merge conflict in app.py
release-2.x  applied, code RUNS -> en-us
release-3.x  already there
release-4.x  applied, code BROKEN -> ImportError: cannot import name 'normalise_locale'
release-5.x  CONFLICT (modify/delete): app.py deleted in HEAD and modified in <fix>
```

5 of 5. The same comparison on a real repository with real history returned 3 of 3.

## It checks itself in front of you

```
Self-check: PASSED (19/19 bundled analyzer cases)
```

Before reading your repository it **builds a small one** in a temporary directory it owns
and removes, feeds it to the shipped analyzer, and prints the result. A failure withholds
the findings. Coverage is itself a case, so a verdict with no positive case fails rather
than passing quietly, and so is discovery, because a run that finds no release branches
looks exactly like a repository with nothing to backport.

Proved by mutation, each restored byte-identically by checksum:

```
THE NAIVE VERSION: merge-tree without --merge-base   -> 15/19
underpinning check removed                          -> 17/19
symbols the patch itself defines no longer excluded -> 17/19
conflict kind no longer read                        -> 17/19
patch-id equivalence check removed                  -> 18/19
merge commits rehearsed anyway                      -> 18/20
git failure folded back into not-a-git-repo         -> 18/19
relative root no longer refused                     -> 18/19
```

## Read-only by construction, not by intention

Every git call passes an allow-list of query subcommands, so a writing verb cannot be
spelled through it at all. All 22 of these are refused before git is invoked:

```
cherry-pick checkout commit merge rebase reset push fetch pull worktree stash
clean gc prune am apply revert branch tag update-ref init clone
```

A full run on a copy of the bundled repository leaves `HEAD`, every ref, and the dirty
file count byte-identical.

## What it does not claim

A clean apply is not proof the branch still works: no test is run and no build is
attempted. The absent-symbol check recognises Python, JavaScript/TypeScript and Go
definition forms only, so a language it cannot read produces no finding rather than a
guess. A patch that landed on a branch **in modified form** — a conflict somebody
resolved by hand — has a different patch id and cannot be told from work that never
landed; that residual is the same one
[lgoyal6/branch-debt](https://play.modiqo.ai/lgoyal6/branch-debt) documents for branch
lifecycle, and this play defers to it for the "is it already landed" question at depth.
It reads local git only, so it never fetches and knows nothing about pull requests.

## Licence

MIT
