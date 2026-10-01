---
name: loop-review
description: Run a bounded, read-only two-model review of code, a design proposal, or an existing review. Use for independent discovery and cross-verification, or targeted verification of fixes from a prior review. Preserve inputs and evidence, and deliver one standalone final review with conclusions, uncertainty and recommended actions.
---

# loop-review

Use the deterministic controller. It owns reviewer calls, budgets, checkpoints and
completion. The reviewed target stays unchanged.

## Run

1. Identify `design`, `code` or `review` mode. Preserve the user's request verbatim.
   Use the existing target file when available; a Git repository is required.
2. Create the [invocation](references/invocation.md). Supply a short meaningful
   `title` for this run and `task.title` for the work being reviewed. Reuse an
   explicit task ID for related work; similar titles alone never establish linkage.
   For multiple required documents, use `target.kind: "files"` with every path.
   Referenced context is not a substitute for full target coverage.
3. Resolve `SKILL_ROOT` to this skill directory and start:
   ```bash
   python3 "$SKILL_ROOT/scripts/loop_review.py" run --invocation <file.json> --json
   ```
   Real runs detach by default. Preserve `run_id` (also returned as `job_id`) and
   `report_path`. Use `--foreground` only for debugging/manual execution.
4. Check the same run:
   ```bash
   python3 "$SKILL_ROOT/scripts/loop_review.py" status --run <run_id> --json
   ```
   While active, report the phase/progress and retain that run ID. Avoid repeated
   short polling and duplicate runs. Use `list --json` to locate a lost ID; inspect
   title, repository and task rather than assuming the global latest is yours.
5. At completion, read the returned `report_path`: **`final-review.md`**. It is the
   standalone deliverable, including the original request, targets, coverage,
   confirmed/contested/unverified/resolved claims, both sides' evidence and next
   actions. A completed review can require changes or leave questions unresolved.
6. Apply [outer acceptance](references/outer-agent-acceptance.md), bounded to the
   original request and material evidence. Finish the same final document in the
   user's language: state the recommendation and its limits, reconcile questions
   answered by cross-check evidence, and distinguish remaining defects from
   implementation checks and incomplete coverage. Preserve finding IDs, reviewer
   positions and uncertainty; explain any evidence-based disposition that differs
   from the ledger. Do not silently change machine results or start another model
   review. Keep `report.md` byte-identical as a compatibility copy. Return a link
   to `final-review.md`; the user or receiving Codex needs only that file.
   Inspect machine/audit files only for a concrete doubt or diagnostic question.

## Related reviews and recovery

- A changed target starts a **new run** under the existing task. Explicitly set
  `previous_run_id` when historical unresolved issues should be checked.
- A full review keeps both discovery prompts independent of prior findings.
  Historical claims enter targeted verification after discovery is persisted.
- For **fixes only**, use `scope: "fixes"` with a previous run. Report this limited
  scope explicitly; it cannot establish a complete review of the current target.
- For an unchanged incomplete run, resume only when the user asks to continue:
  ```bash
  python3 "$SKILL_ROOT/scripts/loop_review.py" resume --run <run_id> --json
  ```
  Successful exact-input checkpoints are reused. Failed attempts, active time and
  known tokens remain charged to the same budget. Config/input drift and exhausted
  budgets are errors; do not bypass them with silent retries or a fresh run.
  A successful terminal rejected by the former CLI-turn counting bug can be
  recovered after exact-input and budget checks. Raw failures stay intact and
  `recovery.json` records corrected accounting; this does not repair missing coverage.
- Cancel an active run with `cancel --run <run_id> --json`. Cancellation is recorded
  and supervised workers stop, including launcher descendants.
- Historic v1 runs are inspection-only. Model/harness changes require a new run;
  there are no model-specific migration exceptions.

## Completion

The controller performs parallel discovery and at most one parallel verification
phase. Empty complete discoveries finish early. New verification findings remain
unverified; disputes terminate without another debate loop. Every terminal path
produces `final-review.md`; partial failures identify incomplete execution and
available evidence, and never receive a passing conclusion. `UNRESOLVED_MAX_CYCLES`
is a compatibility status name: the bounded review ended with questions or
unverified claims, and still delivers final recommendations.

Token budgets reserve verification capacity separately for each reviewer. Live
status and `audit/execution.md` show phase allowances and token breakdowns. A finish
warning asks the reviewer to return partial evidence; coverage questions prevent
an early result from passing. If a phase hits TASK_BUDGET_EXCEEDED, inspect the
partial report and narrow the next review's scope instead of silently retrying.

Human-readable `list` and `status` are available without `--json`. Generated root,
repository and task indexes link the reports. `status.json` is authoritative.

For configuration and artifact organization see the repository README. Consult
[protocol](references/reviewer-protocol.md) for output details and
[state semantics](references/state-machine.md) for checkpoints, limits and status.
