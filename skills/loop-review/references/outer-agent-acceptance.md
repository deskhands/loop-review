# Outer Agent Acceptance

The outer agent that initiated `loop-review` must evaluate the frozen review before presenting it as accepted.

This is a separate layer from Python convergence.

## Responsibilities

The controller decides whether the review process converged and emits one of its review states, such as:

- `FROZEN_PASS`
- `FROZEN_CHANGES_REQUIRED`
- `FROZEN_DISPUTED`
- `UNRESOLVED_MAX_CYCLES`

The outer agent decides whether to accept that review conclusion in the context of the user's original request.

Do not ask either reviewer worker to make this acceptance decision.

## Required inputs

Always inspect:

1. the original user request available in the outer conversation;
2. `final/status.json`;
3. `final/review.md`.

Inspect these when needed to resolve doubt, disagreement, missing evidence, or apparent misunderstanding:

- `final/review.json`
- `state/issue-ledger.json`
- individual `rounds/**/review.md` or `result.json`
- the original target and applicable `AGENTS.md`

The outer agent should not summarize away material user constraints before making the acceptance decision.

## Acceptance statuses

Use exactly one:

### ACCEPTED

Use when the frozen review is materially consistent with the user's original goal, applicable project rules, and the cited evidence.

`ACCEPTED` means the outer agent accepts the **review conclusion**.

It does not necessarily mean the reviewed design/code itself is acceptable.

For example:

- `FROZEN_PASS + ACCEPTED`: accept the conclusion that no blocking issue remains.
- `FROZEN_CHANGES_REQUIRED + ACCEPTED`: accept the conclusion that changes are required.

### REJECTED

Use when the frozen review conclusion itself is materially unsound, for example because it:

- misunderstood the original user goal;
- conflicts with an applicable `AGENTS.md`;
- relies on materially incorrect or missing evidence;
- treats an unjustified assumption as a requirement;
- clearly omits information already available to the outer agent that changes the conclusion.

State the concrete reason for rejection. Do not silently replace the frozen review with a different conclusion.

### NEEDS_FOLLOWUP

Use when the outer agent cannot responsibly accept or reject the review yet, including:

- `FROZEN_DISPUTED` where the remaining disagreement is material;
- `UNRESOLVED_MAX_CYCLES`;
- missing context or evidence that prevents a sound acceptance decision;
- a result that requires a user decision on a genuine tradeoff.

Explain the specific follow-up required.

## Decision discipline

Do not rerun the loop merely because the outer agent personally prefers a different style.

Do not reject a review because it returned `FROZEN_CHANGES_REQUIRED`; that state may be correct and should then be `ACCEPTED`.

Do not equate reviewer agreement with correctness. Verify material conclusions against the original request and evidence.

Do not create a second hidden review loop in the outer layer. The acceptance pass should be bounded: inspect the final result first, drill into ledger/round artifacts only when a concrete doubt requires it.

If the controller status is `FAILED`, do not assign one of the three acceptance statuses. Report the failure code and run directory instead.

## User-facing report

Keep the two layers explicit:

```text
Review status: FROZEN_CHANGES_REQUIRED
Outer acceptance: ACCEPTED

Reason:
The two reviewers converged on F001/F003 with source evidence, the findings match the original request and applicable AGENTS.md, and no material contradiction was found.
```

If rejected:

```text
Review status: FROZEN_PASS
Outer acceptance: REJECTED

Reason:
The review treated an optional future requirement as mandatory, contrary to the original request and the repository's anti-overengineering rule.
```

If follow-up is required:

```text
Review status: FROZEN_DISPUTED
Outer acceptance: NEEDS_FOLLOWUP

Reason:
F002 remains a material correctness dispute with conflicting source evidence; user or implementation owner input is required.
```
