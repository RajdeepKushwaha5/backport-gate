#!/usr/bin/env -S rote play run
/**
 * backport-gate
 *
 * Can this fix be carried onto the release branches you still support?
 *
 * @rote-frontmatter
 * ---
 * name: backport-gate
 * description: 'You fixed a bug on your main branch. You also still support older versions, like 1.x and 2.x. Can the fix go there too? This looks at each of those branches and tells you one of four things. It goes on cleanly. It clashes, and someone will have to sort that out by hand: it names the files, and says whether the clash is two versions of the same file or a file that branch does not have at all, because those need different decisions. It is already there, including when it was copied over earlier and so has a different commit id. Or, the one worth having, it goes on cleanly and would still break that branch, because it uses something that was never added there. On the bundled example that branch really does fail with an ImportError after a copy that git called clean. The obvious way to ask git this question quietly answers a different one: it tells you what would happen if you brought everything since the fix along with it, which makes a broken result look fine. This asks about the one commit on its own, and a bundled test fails if anyone changes it back. Nothing in your repository is touched: no branches, no commits, nothing checked out, and every git command it is able to run is on a read-only list. Run it with no settings at all to see a small example. A clean result is not a promise that the branch still works, because no test is run, and it says so. Needs python3 and git. No network, no accounts, no keys.'
 * provenance:
 *   author: rajdeepkushwaha <rjdprocks9977@gmail.com>
 * source_url: https://github.com/RajdeepKushwaha5/backport-gate
 * tags:
 * - git
 * - backport
 * - release
 * - cherry-pick
 * - maintenance
 * output:
 *   format: markdown
 * parameters:
 * - name: root
 *   param_type: string
 *   required: false
 *   default: demo
 *   description: Absolute path to the repository, or the word demo for the bundled one
 * - name: fix
 *   param_type: string
 *   required: false
 *   default: HEAD
 *   description: The commit to carry back. Any git revision; defaults to HEAD
 * - name: branches
 *   param_type: string
 *   required: false
 *   default: ''
 *   description: Comma-separated branches. Empty discovers release-shaped local branches
 * metadata:
 *   rote_version: 0.79.0
 *   version: 0.1.4
 *   status: released
 *   kind: atomic
 *   flow_type: sequential
 *   execution_model: steps_with_presentation
 *   format: typescript
 *   requires_sessions: false
 *   discoverability:
 *     tags:
 *     - git
 *     - backport
 *     - release
 *     - cherry-pick
 *     - maintenance
 * presentation_fixtures:
 *   selfcheck: resources/presentation-fixtures/selfcheck/fixture.yaml
 *   plan: resources/presentation-fixtures/plan/fixture.yaml
 *   rehearse: resources/presentation-fixtures/rehearse/fixture.yaml
 * steps:
 *   selfcheck:
 *     type: process.exec
 *     timeout_ms: 120000
 *     argv:
 *     - python3
 *     - '@resource{selfcheck.py}'
 *   plan:
 *     type: process.exec
 *     timeout_ms: 120000
 *     argv:
 *     - python3
 *     - '@resource{backport.py}'
 *     - plan
 *     - $root
 *     - $fix
 *     - $branches
 *   rehearse:
 *     type: process.exec
 *     timeout_ms: 180000
 *     depends_on:
 *     - plan
 *     argv:
 *     - python3
 *     - '@resource{backport.py}'
 *     - rehearse
 *     - $root
 *     - '@plan{.stdout.text}'
 * ---
 */

const { FlowOutput, loadPresentationContext, stepName } =
  await import("__ROTE_PRESENTATION_SDK__");

const out = new FlowOutput();
const ctx = await loadPresentationContext();

type Row = {
  branch: string;
  verdict: string;
  detail: string;
  conflict_files?: string[];
  conflict_files_total?: number;
  missing_symbols?: string[];
  symbols_unchecked?: number;
  symbols_over_cap?: number;
};

type Degraded = { step: string; state: string; detail: string };
const degraded: Degraded[] = [];

// Takes the handle, not the name, so every stepName("...") stays a literal lint can verify.
function checkStep(name: string, step: ReturnType<typeof ctx.step>) {
  const o = step.outcome as { status: string; output?: Record<string, unknown> };
  if (o.status !== "completed" && o.status !== "restored") {
    degraded.push({
      step: name,
      state: o.status,
      detail: String(o.output?.reason ?? o.output?.message ?? "no detail recorded"),
    });
    return null;
  }
  return (o.output ?? {}) as { body?: Record<string, unknown> };
}

const selfStep = checkStep("selfcheck", ctx.step(stepName("selfcheck")));
const planStep = checkStep("plan", ctx.step(stepName("plan")));
const rehearseStep = checkStep("rehearse", ctx.step(stepName("rehearse")));

for (
  const [name, st] of [
    ["selfcheck", selfStep],
    ["plan", planStep],
    ["rehearse", rehearseStep],
  ] as const
) {
  if (!st) continue;
  const body = (st.body ?? {}) as { stdout?: { truncated?: boolean; bytes?: number } };
  if (body.stdout?.truncated === true) {
    degraded.push({
      step: name,
      state: "truncated",
      detail:
        `stdout was cut at rote's 64 KiB preview ceiling (${body.stdout?.bytes ?? "?"} bytes produced)`,
    });
  }
}

function textOf(st: { body?: Record<string, unknown> } | null): string {
  const body = (st?.body ?? {}) as { stdout?: { text?: string } };
  return body.stdout?.text ?? "";
}

const BLURB: Record<string, string> = {
  CONFLICTS:
    "Cherry-picking this commit onto the branch stops and asks you to resolve something. The files are named. This is git's own three-way merge, run in the form that writes nothing — not a guess from shared file names.",
  CLEAN_BUT_UNDERPINNED:
    "The patch applies with no conflict **and the branch would still be broken**: it calls something that exists on the fix's side and nowhere on this branch. A clean exit code is exactly what hides this.",
  APPLIES_CLEAN:
    "The patch applies with no conflict, and everything its new lines call exists on the branch. Not a claim that the branch still behaves correctly — only a test can say that.",
  ALREADY_PRESENT:
    "The change is already on the branch. Either this exact commit is an ancestor, or an equivalent patch landed under a different hash and was matched by patch id.",
  NOT_APPLICABLE:
    "Nothing was rehearsed, and why is stated. A merge commit needs a parent chosen by hand, and this play will not choose one for you.",
  UNCHECKED:
    "git did not answer for this branch. This is the play failing to look, not a finding about the branch.",
};

const ORDER = [
  "CONFLICTS",
  "CLEAN_BUT_UNDERPINNED",
  "APPLIES_CLEAN",
  "ALREADY_PRESENT",
  "NOT_APPLICABLE",
  "UNCHECKED",
];

let selfPassed = 0;
let selfTotal = 0;
let selfFailures: { case: string; detail: string }[] = [];
if (selfStep) {
  try {
    const parsed = JSON.parse(textOf(selfStep)) as {
      passed: number;
      total: number;
      failures: { case: string; detail: string }[];
    };
    selfPassed = parsed.passed;
    selfTotal = parsed.total;
    selfFailures = parsed.failures ?? [];
  } catch {
    selfFailures = [{ case: "selfcheck", detail: "did not return JSON" }];
    selfTotal = 1;
  }
}
const selfResult = { passed: selfPassed, total: selfTotal, failures: selfFailures };
const selfOk = selfTotal > 0 && selfFailures.length === 0 && selfStep !== null;
const selfLine = selfOk
  ? `Self-check: PASSED (${selfPassed}/${selfTotal} bundled analyzer cases)`
  : `Self-check: FAILED (${selfPassed}/${selfTotal} bundled analyzer cases)`;

if (degraded.length > 0) {
  out.human(
    `# Run incomplete\n\nA step did not finish, so no verdict is offered. A partial rehearsal must never read as a clean one.\n\n${
      degraded.map((d) => `- **${d.step}** (${d.state}) — ${d.detail}`).join("\n")
    }`,
  );
  out.summary(`incomplete: ${degraded.map((d) => d.step).join(", ")}`);
  out.result({ status: "incomplete", degraded, self_check: selfResult });
} else if (!selfOk) {
  // The analyzer could not reproduce its own bundled cases, so its findings are
  // withheld rather than dressed up. A wrong backport verdict costs a release.
  out.human(
    `${selfLine}\n\n# Findings withheld\n\nThe shipped analyzer failed its own bundled cases, so nothing it would say about your repository is trustworthy on this run.\n\n${
      selfFailures.map((f) => `- **${f.case}** — ${f.detail}`).join("\n")
    }`,
  );
  out.summary(`self-check failed: ${selfPassed}/${selfTotal}`);
  out.result({ self_check: selfResult, findings_withheld: true });
} else {
  let parsed: {
    status: string;
    detail?: string;
    root_label?: string;
    discovery?: string;
    fix?: { short: string; subject: string; files: string[] };
    results?: Row[];
    counts?: Record<string, number>;
    branches_not_examined?: number;
  };
  try {
    parsed = JSON.parse(textOf(rehearseStep) || textOf(planStep));
  } catch (cause) {
    throw new Error("rehearse stdout was not valid JSON", { cause });
  }

  if (parsed.status !== "ok") {
    const why: Record<string, string> = {
      "not-a-git-repo": "That path is not inside a git repository.",
      "git-unavailable":
        "git could not be run, so nothing was examined. This says nothing about the repository itself.",
      "no-such-commit": "No commit matched what you asked to carry back.",
      "no-branches":
        "No maintained release branch was found or named. Pass branches=a,b to name them yourself.",
      "fix-is-root-commit": "A root commit has no parent, so there is no patch to carry.",
    };
    out.human(
      `${selfLine}\n\n# Nothing could be rehearsed\n\n${why[parsed.status] ?? parsed.status}${
        parsed.detail ? `\n\n${parsed.detail}` : ""
      }`,
    );
    out.summary(`not rehearsed: ${parsed.status}`);
    out.result({ status: parsed.status, self_check: selfResult });
  } else {
    const rows = parsed.results ?? [];
    const counts = parsed.counts ?? {};
    const fix = parsed.fix!;
    const blocked = (counts["CONFLICTS"] ?? 0) + (counts["CLEAN_BUT_UNDERPINNED"] ?? 0);

    const sections: string[] = [];
    sections.push(
      `# Carrying \`${fix.short}\` back${
        String(parsed.root_label ?? "").startsWith("demo") ? " in the bundled example" : ""
      }: ${rows.length} branch(es), ${blocked} that need work\n\n> ${fix.subject}\n\nRepository: ${parsed.root_label}. Branches ${parsed.discovery}. The commit touches ${fix.files.length} file(s).`,
    );

    for (const v of ORDER) {
      const group = rows.filter((r) => r.verdict === v);
      if (group.length === 0) continue;
      const lines = group.map((r) => {
        let s = `- \`${r.branch}\` — ${r.detail}`;
        if (r.conflict_files && r.conflict_files.length > 0) {
          const total = r.conflict_files_total ?? r.conflict_files.length;
          const extra = total > r.conflict_files.length
            ? ` (and ${total - r.conflict_files.length} more)`
            : "";
          s += `\n  conflicts in: ${
            r.conflict_files.map((f) => `\`${f}\``).join(", ")
          }${extra}`;
        }
        if (r.missing_symbols && r.missing_symbols.length > 0) {
          s += `\n  **absent from this branch:** ${
            r.missing_symbols.map((m) => `\`${m}\``).join(", ")
          }`;
        }
        if (r.symbols_unchecked && r.symbols_unchecked > 0) {
          s +=
            `\n  ${r.symbols_unchecked} symbol(s) could not be checked, and are not a finding either way`;
        }
        return s;
      });
      sections.push(`## ${v} (${group.length})\n${BLURB[v]}\n${lines.join("\n")}`);
    }

    if ((parsed.branches_not_examined ?? 0) > 0) {
      sections.push(
        `## Not examined (${parsed.branches_not_examined})\nMore release branches exist than were rehearsed. This report is partial, and that is a limit of this play rather than a quiet repository.`,
      );
    }

    sections.push(
      `## What this does not claim\nA clean apply is not proof the branch still works: no test was run and no build was attempted. The absent-symbol check recognises Python, JavaScript/TypeScript and Go definition forms only, so a language it cannot read produces no finding rather than a guess. A patch that landed on a branch in modified form — a conflict somebody resolved by hand — has a different patch id and cannot be told from work that never landed, the same residual \`lgoyal6/branch-debt\` documents for branch lifecycle. Nothing here was checked out, cherry-picked or written.`,
    );

    out.human(`${selfLine}\n\n${sections.join("\n\n")}`);
    out.summary(
      `${rows.length} branch(es): ${
        ORDER.filter((v) => counts[v]).map((v) => `${counts[v]} ${v.toLowerCase()}`)
          .join(", ")
      }`,
    );
    out.result({
      status: "ok",
      fix: { short: fix.short, subject: fix.subject },
      counts,
      results: rows,
      branches_not_examined: parsed.branches_not_examined ?? 0,
      self_check: selfResult,
      // three views of one run. The listing is capped in the
      // human view, so which view is canonical is stated here
      // rather than left for a reader to discover.
      representations: {
        human:
          "complete for the branches rehearsed: every branch with its verdict, the files a conflict would touch, and the symbols an underpinned branch is missing",
        json:
          "canonical superset: the same branches plus the self-check result, the commit identity, the counts and how many branches were not examined",
        summary: "intentionally lossy: branch counts by verdict",
      },
    });
  }
}
