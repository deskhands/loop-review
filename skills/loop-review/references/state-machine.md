# State machine

Flow:

`INIT -> NORMALIZE_INPUT -> DISCOVER_RULES -> FINGERPRINT -> PREFLIGHT -> DISCOVERY_PARALLEL -> BUILD_LEDGER -> CROSS_CHECK_1 -> [FREEZE | CROSS_CHECK_2] -> [FREEZE | DISPUTED | UNRESOLVED]`

Any infrastructure/protocol failure goes to `FAILED`.

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
