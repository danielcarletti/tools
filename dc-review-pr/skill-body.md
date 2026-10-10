# dc-review-pr

Review one PR against the checklist below. Read the diff, judge only what the
diff changes (plus the files it touches), and report findings.

## Delivery mode

Read the delivery mode from the task prompt (default: `report-only`).

### report-only (default)

Output the report as your final message. **Do not post to GitHub, comment,
approve, or push.**

### github

Post the full findings report as **one PR comment** on GitHub, then output the
same report as your final message:

1. Build the report first (see Output format).
2. Post it with `gh pr comment <N> --repo <owner/repo> --body-file -` (pipe the
   report markdown on stdin) or `--body "<report>"`.
3. If there are no actionable findings, post the literal text `No comments`.
4. Do **not** approve, request changes, or push.

### github-inline

Post **each finding** as an inline review comment on the cited line, then
output the report as your final message:

1. Build the report first (see Output format). Each finding must include an
   exact `file:line` anchor.
2. Get the PR head commit: `gh pr view <N> --repo <owner/repo> --json headRefOid`.
3. Submit one review with inline comments via the GitHub API:

   ```bash
   gh api repos/<owner>/<repo>/pulls/<N>/reviews \
     -f commit_id=<headRefOid> \
     -f event=COMMENT \
     -f body='<2-3 sentence summary from Verdict>' \
     -f 'comments=[{"path":"<file>","line":<line>,"side":"RIGHT","body":"[<severity>] <problem>. Fix: <fix>"}]'
   ```

   One JSON object per finding in the `comments` array. Use `side=RIGHT` (the
   PR head version). Keep each comment body concise — no markdown tables.

4. Line anchoring: the `line` must exist on the PR head commit. When a finding
   cites `file:line`, read that file at `headRefOid` to confirm the line
   number. If the cited line isn't in the diff or can't be anchored reliably,
   include that finding in the review `body` summary instead of guessing a line.
5. If there are no actionable findings, **do not** call the review API — just
   output `No comments` as your final message.
6. Do **not** approve, request changes, or push.

## Steps

1. Get the PR number and repo from the task prompt.
2. `gh pr view <N> --repo <owner/repo> --json title,body,files,additions,deletions`
   and `gh pr diff <N> --repo <owner/repo>`. Open any changed file with Read
   when the diff alone is ambiguous.
3. Walk every checklist section. Report only actionable findings: each =
   `file:line` + one-line problem + concrete fix. Never write affirming notes
   explaining why something is fine — the absence of a comment is the signal the
   PR is good on that topic. A clean section produces nothing.
4. Deliver per the delivery mode above, then emit the report as your final
   message.

## Checklist

**Comments**

- Too long or too complex — should be shorter or unnecessary.
- Comment disagrees with what the code actually does.

**Naming**

- Names that say nothing (`x`, `y`, `tmp`, `data`).
- The same name reused for distinct things — e.g. `counter` (or `i`, `item`)
  across two or more separate loops. Each gets its own descriptive name.
- Name doesn't match what the thing is or does.

**Control flow**

- Boolean expressions that can be simplified.
- Logic that's plain wrong — scrutinize boolean expressions hardest.
- A loop where direct access would do — iterating to reach one element or a
  known slice instead of `items[0]` / `items[:n]`. Common in LLM-written code;
  flag it.
- Wrong loop stop condition (off-by-one, never terminates, skips elements).

**Scope & side effects**

- Title/description doesn't describe what the code does.
- Side effects in the diff that the description doesn't mention.
- Could those side effects break something elsewhere? Name what.
- Does the change actually achieve its stated goal?

**Modularity**

- A function/class that should split into smaller, testable pieces.
- Code in the wrong file or violating single-responsibility — say where it belongs.

**DRY**

- Logic duplicated here or already existing elsewhere — point to what to reuse.

**Tests**

- Trivial/pointless tests, or duplicates testing the same scenario.
- Over-complicated tests.
- Missing or insufficient tests for the new behavior.
- Behavior that needed manual testing with no evidence it was done.
- Frontend changes with no screenshot.

**Dead code**

- Code added but unreachable/unused, or left behind after the change.

**Performance & cost**

- APIs returning excessive data.
- APIs reused in a wasteful way (redundant calls, over-fetching).
- High CPU/memory operations.
- Out-of-memory risks (unbounded loads, large in-memory buffers).
- High cloud-cost patterns (e.g. rewriting many S3 objects).

## Output format

```
## Review: PR #<N> — <title>

**Goal achieved:** yes | no | partial — <one line>
**Undisclosed side effects:** none | <list>

### Findings
- [severity] file:line — <problem>. Fix: <concrete suggestion>.
...

### Verdict
<2-3 sentences: overall quality and the must-fix items, if any>
```

Severity: `blocker` (correctness/breakage/cost risk), `should-fix` (quality), `nit`
(minor). Omit clean sections entirely. If nothing is actionable anywhere, the
whole report is just `No comments`.
