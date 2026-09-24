---
name: loop-review
description: Orchestrate an auditable, read-only multi-model review loop for design proposals, code changes, or an existing review. Use when the user asks for loop-review, wants a design/code review to converge across Grok and DeepSeek, or wants an existing review independently verified. The skill records original inputs without summarizing them, enforces applicable AGENTS.md rules on every round, runs blind parallel discovery followed by bounded serial cross-checks, and returns a frozen review status without modifying the reviewed target.
---

# loop-review

Run the deterministic controller; do not reproduce its orchestration in conversation.

## Workflow

1. Identify the mode: `design`, `code`, or `review`.
2. Preserve the user's request and supplied target text verbatim. Do not summarize or rewrite them.
3. Determine the repository root. V1 requires the supplied repository to be a Git repository for every mode so repository state can be fingerprinted and audited.
4. Build an invocation JSON matching [references/invocation.md](references/invocation.md).
5. Resolve `SKILL_ROOT` to the directory containing this `SKILL.md`, then run:
   ```bash
   python3 "$SKILL_ROOT/scripts/loop_review.py" run --invocation <invocation.json>
   ```
6. Read the returned `status_path` and `review_path`.
7. Perform the required **Outer Agent Acceptance** described in [references/outer-agent-acceptance.md](references/outer-agent-acceptance.md). The controller's `FROZEN_*` status is a frozen review conclusion, not automatic acceptance by the outer agent.
8. Report both the controller review status and the outer acceptance status to the user, keeping them distinct.
9. If the controller fails, report its failure code and run directory. Do not bypass a failed reviewer or silently fall back to one model.

The controller owns prompt construction, reviewer ordering, timeouts, state transitions, evidence retention, and freeze decisions. Grok/DeepSeek workers must not be called separately as a substitute for the controller. The outer agent alone owns acceptance of the frozen review in the context of the original user request.

## Input rules

- If the user supplied a design/review body in chat, place the exact text in the invocation as `kind = "text"`; the controller persists it.
- If the target already exists as a local file, pass `kind = "file"` and its absolute path. Do not duplicate large files.
- For `review` mode, provide both the original target and the seed review.
- V1 requires `repo` to resolve to a Git repository in every mode. For `code` mode, use `working-tree` unless the user explicitly specifies a Git range.
- Never modify the reviewed target to make the review pass.

## Mandatory behavior

- Treat every applicable `AGENTS.md` as binding. The controller discovers and fingerprints them; every worker must read the original files on every round.
- Preserve fresh worker contexts. Do not carry interactive sessions between rounds.
- Keep the review read-only. Workers receive only read/search capabilities.
- Let the controller freeze only the review conclusion. `FROZEN_CHANGES_REQUIRED` is a valid successful review outcome.
- Require outer-agent acceptance after every non-failed frozen/unresolved result. Do not treat model convergence as authority over the user's original goal.
- Keep all run artifacts under the configured run root for audit.

For schemas and semantics, consult:
- [references/invocation.md](references/invocation.md)
- [references/reviewer-protocol.md](references/reviewer-protocol.md)
- [references/state-machine.md](references/state-machine.md)
- [references/outer-agent-acceptance.md](references/outer-agent-acceptance.md)

## Local commands

Resolve `SKILL_ROOT` to the directory containing this `SKILL.md`.

Health check:
```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" doctor
```

Prepare a run without model calls:
```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" run --invocation <invocation.json> --dry-run
```

Run tests:
```bash
python3 -m unittest discover -s "$SKILL_ROOT/tests" -v
```
