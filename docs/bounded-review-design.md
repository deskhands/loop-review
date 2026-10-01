# Bounded review workflow

## Scope

Refactor review orchestration and its human-facing artifacts. Keep the configured
Qwen/Pi and DeepSeek/Claude models unchanged; model/provider selection is phase two.
Use the standard library, local files and subprocesses. No server, database,
generic workflow framework or new autonomous reviewer loop.

## Public behavior

- Store runs outside reviewed repositories under the configured `run_root`.
- Group by Git common-directory identity (worktrees together, clones separately),
  explicit task identity and immutable run identity. Names include readable titles.
- Generate root/repository/task indexes linking reports. A single root `status.json`
  per run is authoritative; `report.md` and `result.json` are projections.
- `run`, `list`, `status`, `resume`, `cancel` have readable default output and `--json`
  for callers. Detached processes use the run ID as the job ID.
- Preserve original inputs, fingerprints and raw attempt events. Historic v1 runs
  remain inspectable; v2 does not replay their different workflow or migrate models.

## Finite workflow

1. Two parallel discovery tasks independently examine the current input. Neither
   prompt includes peer results or prior run findings.
2. Persist discoveries before constructing verification prompts. Each reviewer
   verifies the other reviewer's claims. Verification can report an essential new
   issue as unverified, but never starts another discovery cycle.
3. When an explicit `previous_run_id` is supplied, add only unresolved historical
   claims to verification. An explicit `fixes` scope skips discovery and verifies
   those claims; its report cannot claim a complete review of the target.
4. Finish with confirmed findings, disagreements and unverified findings. Empty,
   complete discoveries can finish after two tasks. Normal full reviews use at
   most four tasks. Disagreement is a terminal result, not a reason to keep calling.

## Protocol and recovery

Workers return a small v2 contract: summary, policy paths read, findings,
verification decisions and open questions. The controller assigns finding IDs,
derives rule-violation state and owns final status. Workers do not maintain local
to canonical ID relations, duplicate chains or freeze decisions. Claims always
need evidence; lack of a repeated finding never proves a historical fix.

Resume only reuses a checkpoint with an exact call-input digest: current input
fingerprint, prompt, reviewer model/reasoning/adapter and protocol version. Changed
inputs/configuration require a new run. Valid raw terminal responses may be
recovered with no additional model task. Failed attempts remain immutable.

## Resource control

Count actual task attempts, including failed attempts, across resume. Enforce
per-task wall time, assistant turns, tool calls, repeated identical calls, provider
retries and prompt/output size. Enforce cumulative known token consumption and
active execution time across the run; waiting between resumes is not active time.
Live usage is observed at event boundaries, so an in-flight provider request can
overshoot the token ceiling. Unavailable usage/cost is explicitly unknown.

Use the existing total-token setting to derive equal reviewer shares and fixed
3:1 discovery/verification allocations. Fixes-only verification gets the full
reviewer share. Do not lend unused tokens between workers or phases. Enforce each
allocation over all attempts, stopping only the exhausted worker; retain the
global time/cancel/token guard. Default total is 8M including cached tokens.

Provide a private live budget file per reviewer/phase. At 75% of known tokens,
turns or tools, request final output with uncovered scope in open questions. A
controller-added coverage question prevents a resource-limited result from passing
silently, including checkpoint recovery. This is cooperative finishing, not a new
model call or a guarantee of graceful completion. Keep hard caps and immutable
attempts. Cache/input/output breakdowns explain consumption separately from cost.

Stream stdout/stderr to attempt files immediately, update compact live progress,
and terminate the entire worker process group on timeout/cancellation/limits.
Do not automatically restart failed tasks. A user-requested resume consumes the
remaining budget. On failure, render partial discoveries and verification results
as incomplete, never as a passing review.

## Validation interfaces

Exercise existing public interfaces: controller `run`/`resume`, adapter `review`
and CLI commands. Cover repository/worktree grouping, readable names, explicit
task/run linkage, blind discovery, targeted historical verification, four-task
termination, exact checkpoint reuse, cumulative resume limits, live stream writes,
timeout/cancel process cleanup and partial failure reports. Keep Git fingerprint,
read-only adapter and provider/parser regression coverage. Use fake reviewers and
local child processes; no paid model calls are required for deterministic checks.
