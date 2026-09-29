# State machine

Flow:

`INIT -> NORMALIZE_INPUT -> DISCOVER_RULES -> FINGERPRINT -> PREFLIGHT -> DISCOVERY_PARALLEL -> BUILD_LEDGER -> CROSS_CHECK_1 -> [FREEZE | CROSS_CHECK_2] -> [FREEZE | DISPUTED | UNRESOLVED]`

Any infrastructure/protocol failure goes to `FAILED`.

A real `run` starts the same controller in a new local process session by default and returns a durable `job_id`; `--foreground` is reserved for explicit debugging/manual synchronous execution, while `--dry-run` stays synchronous. `status --job <job_id>` reads the detached job; `status` without a job id resolves the most recent detached job. An outer-agent disconnect does not change controller state. If the detached process itself disappears before writing a terminal result, status is `ORPHANED`; automatic recovery from an unknown half-executed process is intentionally not attempted.

A `FAILED` job can re-enter the same state machine with `resume --job <job_id>`. Resume does not trust the last persisted ledger as its starting point. It reloads the original invocation and fingerprint, verifies the repository/target/AGENTS.md plus reviewer model/reasoning are unchanged, then deterministically replays round checkpoints in the normal reviewer order. Adapter changes fail closed except for the audited legacy Reviewer-A OpenCode-to-Claude migration of the same canonical GLM model; that migration is recorded in resume metadata and forces a fresh preflight.
- a currently valid `result.json` is reused with no model call;
- if a completed reviewer process exited 0 and its saved `raw.stdout` now parses and validates, the result is recovered with no model call;
- otherwise only that missing/failed reviewer slot is called again;
- discovery remains blind and cross-check uses the same cycle ordering as a fresh run.

Before a resume, the previous terminal state is copied under `run_dir/resume/<timestamp>/`. Before an actual reviewer retry overwrites a failed slot, that slot's prior artifacts are copied under its `attempts/<timestamp>/`. `review_calls` remains the cumulative count of actual reviewer model attempts, so repeated recovery attempts can make it exceed the original logical `max_model_calls` budget; the bounded cycle topology itself does not change.

Cross-check ledger updates are transactional and order-independent. The controller assigns stable IDs to new findings first, resolves `REFINE`/`DUPLICATE_OF` relations to canonical targets (flattening duplicate/superseded chains), validates the full relation graph for unknown targets and cycles, then commits the batch atomically. A failed relation batch leaves the prior ledger unchanged.

`state/state.json` intentionally records coarser durable checkpoints (`INIT`, `PREPARED`, `PREFLIGHT`, `DISCOVERY_PARALLEL`, `CROSS_CHECK`, then the final state). The flow above describes the internal logical phases; the persisted checkpoint names are not a one-to-one trace of every helper step.

## Finding states

- `OPEN`: only one independent reviewer position exists.
- `ACCEPTED`: latest positions from both reviewers accept the finding.
- `REJECTED`: latest positions from both reviewers reject the finding.
- `DISPUTED`: latest positions disagree.
- `SUPERSEDED`: replaced by a refined finding.
- `DUPLICATE`: explicitly linked to another stable finding.

Discovery origin counts as that reviewer's ACCEPT position.

## Convergence

Converged after a complete serial cycle when:
- no new finding was created during the cycle
- no `OPEN` or `DISPUTED` finding remains
- every worker confirmed every required `AGENTS.md`
- target/repository fingerprint is unchanged

## Final states

- `FROZEN_PASS`: converged and no accepted blocking finding.
- `FROZEN_CHANGES_REQUIRED`: converged with one or more accepted blocking findings.
- `FROZEN_DISPUTED`: max cycles reached with disputed findings.
- `UNRESOLVED_MAX_CYCLES`: max cycles reached with open/new findings.
- `FAILED`: infrastructure, target-drift, read-only, timeout, or protocol failure.

The frozen object is the review conclusion, not a repaired target.
