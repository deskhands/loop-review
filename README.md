# loop-review

A read-only two-model review of code, design proposals or existing review claims.
Two independent discoveries are followed by targeted cross-verification. The
controller finishes with confirmed, disputed or unverified findings; it does not
keep debating until models agree or modify the reviewed target.

## Install with npx skills

The `skills` CLI supports GitHub repository shorthand and discovers valid `SKILL.md` files automatically.

Interactive install:

```bash
npx skills add deskhands/loop-review
```

List discoverable skills without installing:

```bash
npx skills add deskhands/loop-review --list
```

Install `loop-review` globally and non-interactively:

```bash
npx skills add deskhands/loop-review --skill loop-review -g -y
```

Install it for selected agents:

```bash
npx skills add deskhands/loop-review \
  --skill loop-review \
  -g \
  -a claude-code \
  -a codex \
  -a opencode \
  -y
```

You can also install directly from the Skill path:

```bash
npx skills add https://github.com/deskhands/loop-review/tree/main/skills/loop-review -g
```

## Requirements and configuration

Python 3.11+, Git, Pi CLI and Claude Code CLI. Use project/machine-managed runtimes;
configuration is `~/.config/loop-review/config.toml`. Start from
[config.example.toml](skills/loop-review/assets/config.example.toml).

Current model choices are unchanged: Qwen3.8 Flash via Pi/OpenRouter, and DeepSeek
via the provider-isolated Claude launcher. Model selection is deferred to phase two.
Use provider-native authentication; configuration and artifacts contain no keys.
Existing v1 configuration is accepted with bounded-workflow defaults; old `loop`
fields no longer drive iterations. New configuration uses `[limits]`.

## Organizing results

All runs are outside reviewed repositories, under configured `run_root` (default
`~/code/agents-tmp/loop-review`). Same Git common directory groups worktrees;
separate clones are distinct. A repository-name suffix is used only for collisions.

```text
loop-review/
├── README.md
└── DeskHands-next/
    ├── repo.json
    ├── README.md
    └── 运行时迁移/
        ├── task.json
        ├── README.md
        └── 2026-09-30_193000__design__修改方案复核__a1b2c3d4/
            ├── report.md
            ├── status.json
            ├── result.json
            ├── manifest.json
            ├── input/
            └── audit/
```

Titles identify the purpose; IDs identify exact runs. The same run ID is used for
background job commands. Root/repository/task indexes link reports and show the
latest known phase or conclusion. `status.json` is authoritative; controller logs
and prior resume snapshots never override it.

Supply `task.id`, `task.title`, and a run `title` in the
[invocation](skills/loop-review/references/invocation.md). A matching title alone
never merges unrelated tasks. Changed code/design creates a new run under the task.
An unchanged failed run resumes in place with immutable raw attempts.

## Commands

Resolve `SKILL_ROOT` to `skills/loop-review` or its installed location.

```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" run --invocation invocation.json
python3 "$SKILL_ROOT/scripts/loop_review.py" list
python3 "$SKILL_ROOT/scripts/loop_review.py" list --repo /absolute/repository
python3 "$SKILL_ROOT/scripts/loop_review.py" status --run a1b2c3d4
python3 "$SKILL_ROOT/scripts/loop_review.py" cancel --run a1b2c3d4
python3 "$SKILL_ROOT/scripts/loop_review.py" resume --run a1b2c3d4
```

Add `--json` for agent/program callers. `--job` is an alias for `--run`.
Real execution detaches by default; `--foreground` is for manual/debug runs.
`--dry-run` prepares readable inputs/prompts without model calls. `doctor` explicitly
performs small provider health checks; it is not automatically run for every review
and does not establish long-task reliability.

## Historical fixes and independence

Task membership is organizational only. Set `previous_run_id` to link a prior
terminal v2 run of the same repository/task. Full-review discovery receives no prior
findings. Only unresolved historical claims are supplied to targeted verification,
with evidence rechecked against current source. Both reviewers must refute a
historical claim to mark it RESOLVED; absence from discovery does not prove a fix.

`scope: "fixes"` verifies specified historical claims without discovery. Its
`FIXES_VERIFIED` outcome is limited scope, never a complete review of current code.

## Limits, failures and audit

Normal full reviews use two discovery tasks and up to two verification tasks.
Empty complete discoveries finish after two tasks. Verification does not recursively
expand: new defects remain unverified, and disagreement is a terminal outcome.
Open questions prevent a passing conclusion.

Default limits include six **total task attempts across resume**, 32 turns per
task, 96 tool calls, three identical calls, two exposed provider retries, eight million
known cumulative tokens and 30 minutes of cumulative active execution. Limits can
be configured before starting a run. Token totals include cached reads; an in-flight
request may overshoot the ceiling. Unavailable usage/cost is explicitly unknown,
and CLI-reported costs are not necessarily provider billing.

The token ceiling is split equally between reviewers, with three quarters of each
share for discovery and one quarter reserved for verification: 3M + 1M per reviewer
by default. Fixes-only runs use the entire 4M share for verification. Failed attempts
and resume consume the same phase allocation; unused allocations are not borrowed.
Exhausting a phase stops that worker with `TASK_BUDGET_EXCEEDED`, allowing the peer
to finish. Cancellation, active-time and total-run limits still stop both workers.

Each reviewer reads only its own `audit/<phase>/<reviewer>/live-budget.json`, which
shows remaining allowance without revealing peer progress. At 75% of tokens, turns
or tools, it requests concise output and records a coverage follow-up question;
such results cannot silently become a passing review. This finish request requires
model cooperation; the hard ceiling remains enforced by the controller. Budget
polls count as tools but are exempt from identical-read detection. Prompts guide
focused auxiliary reads while requiring complete target and AGENTS.md coverage.

Events and stderr are written during execution. Live status shows phase, task
attempts, active time, last event, turns, tools and known usage. Timeouts, cancellation
and limits terminate entire worker process groups. Failures preserve successful
peer evidence and generate an incomplete report.

Turn limits use observed assistant calls. Claude's terminal `num_turns` is a
separate audit counter; it can differ when a response invokes several tools.
Successful terminal output rejected by the former counter mismatch can be recovered
after replay and exact-input validation, without repeating discovery. Original
failure artifacts remain intact and `recovery.json` records the corrected usage.

Multi-document design reviews use `target.kind: "files"` with every required path;
all files and their nested AGENTS.md rules are fingerprinted. One representative
target with other documents mentioned only in prose is insufficient scope control.

Resume reuses only exact call-input checkpoints (prompt, repository/target/policy
fingerprint, reviewer configuration and protocol), validates checksums, and may
recover completed raw output without a new task. It keeps cumulative consumption.
Changed inputs/configuration or exhausted budgets fail closed. Historical v1 runs
remain listed/readable but are not resumed using v2 semantics.

Review data may contain source, requests and sensitive paths; keep the run root
outside version control. Workers are restricted to read/search tools. Discovery
independence is enforced by supplied context/protocol, not an OS filesystem sandbox.
A frozen conclusion still needs bounded
[outer acceptance](skills/loop-review/references/outer-agent-acceptance.md).

## Development

The implementation uses the Python standard library, with no daemon/database or
workflow framework. The first-phase design is
[bounded-review-design.md](docs/bounded-review-design.md).

```bash
python3 -m unittest discover -s skills/loop-review/tests -v
```

Deterministic tests use fake reviewers and local process fixtures; no paid provider
calls are required. They cover workflow termination, history isolation, checkpoint
recovery, cumulative budgets, stream persistence, process cleanup, artifact grouping
and CLI background execution. Provider reliability/model evaluation is phase two.
