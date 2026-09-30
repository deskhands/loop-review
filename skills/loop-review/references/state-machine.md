# State and recovery

`INIT -> PREPARED -> DISCOVERY -> [VERIFICATION] -> terminal`

Fix-only scope skips discovery. No findings plus no open questions can complete
without verification. Each verification checks only the other reviewer's claims
and explicit historical claims. Essential new findings remain UNVERIFIED.

- FROZEN_PASS: complete full review with no confirmed blocking findings.
- FROZEN_CHANGES_REQUIRED: confirmed blocking findings exist.
- FROZEN_DISPUTED: reviewers disagree; review finishes with the evidence positions.
- UNRESOLVED_MAX_CYCLES: unverified findings or open questions (legacy public name;
  v2 does not run cycles).
- FIXES_VERIFIED: specified historical claims refuted/fixed by both reviewers;
  limited scope, never a full-review pass.
- FAILED / CANCELED / ORPHANED: incomplete execution with partial evidence retained.
- PREPARED_DRY_RUN: inputs/prompts prepared with no reviewer calls.
- RESUMING: a detached recovery has started; old terminal output is not current state.

A discovery origin counts as ACCEPT. A peer ACCEPT confirms it; REJECT disputes it.
Historical claims require two current verification positions. Both REJECT means
RESOLVED, both ACCEPT means still present, disagreement means DISPUTED. Missing or
UNCERTAIN positions mean UNVERIFIED. Absence in new discovery never means fixed.

`status.json` is the sole current status. Reports, results and generated indexes
are projections. A process lock prevents concurrent controllers for the same run.
Detached controller logs are diagnostic, never a source of current status.

Resume keeps the run ID, archives the previous status/result and replays the finite
workflow. Every reused checkpoint must match the exact prompt/input fingerprint,
reviewer configuration and protocol, and a saved result checksum. Completed raw
responses may be recovered without another task. Changed source/configuration
fails closed; v1 workflows are inspection-only, with no migration exceptions.

Limits apply to all attempts across resume. Known token usage includes cached
reads; missing usage/cost stays unknown. Tokens are observed at event boundaries,
so one in-flight request may overshoot a limit. Provider retries are capped where
exposed as events. Wall time, turns, tools, repeated calls, prompt/output size and
cancellation also bound tasks. Active wall time is checkpointed during supervision;
waiting between resumes does not spend active time. No automatic full-task retries.

Token allocations are fixed: each reviewer receives half the run ceiling, with
75% for discovery and 25% reserved for verification. Fixes-only verification gets
the entire reviewer share. Every attempt consumes its phase allowance. A phase
ceiling raises TASK_BUDGET_EXCEEDED for that worker; the peer may finish. The run
then reports FAILED with partial evidence. Global limits/cancellation still stop
both workers. Unused allowances are not transferred.

Private live-budget.json files contain only their reviewer's phase allowance and
usage. At 75% of tokens, turns or tools, action becomes finish. This asks the model
to return evidence and missing coverage; the controller also adds an open question
so early output cannot imply a complete pass. Hard limits remain independent of
cooperation. Changed budget policy/prompts require a new run, not legacy resume.

Raw stdout/stderr are streamed into immutable attempt directories under `audit/`.
Cancellation/timeouts terminate worker process groups and preserve partial events.
A disappeared detached controller becomes ORPHANED on status inspection. Resume
verifies inputs and checkpoints; it never trusts a partially updated final ledger.
