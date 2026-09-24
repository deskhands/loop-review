# loop-review

`loop-review` is an auditable, read-only multi-model review Skill for design proposals, code changes, and existing review results.

It orchestrates two independent reviewer workers, builds a structured issue ledger, performs bounded cross-check cycles, freezes the review conclusion deterministically, and then returns control to the outer agent for final acceptance.

## Why

A single reviewer can miss defects or over-trust its own assumptions. `loop-review` uses:

- blind independent discovery by two different models;
- mandatory `AGENTS.md` compliance on every round;
- structured JSON results instead of parsing Markdown as machine state;
- a deterministic Python controller for state, convergence, and failure handling;
- read-only reviewer permissions;
- persisted prompts, raw outputs, findings, and final reports for audit;
- an outer-agent acceptance step after the review conclusion is frozen.

The Skill does **not** modify the reviewed design or source code.

## Repository layout

```text
.
├── README.md
└── skills/
    └── loop-review/
        ├── SKILL.md
        ├── agents/
        ├── assets/
        ├── references/
        ├── scripts/
        └── tests/
```

The Skill lives under `skills/loop-review/` so standard Agent Skills tooling can discover it without treating repository documentation as part of the Skill.

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

## Runtime requirements

Installing the Skill does not install its external reviewer CLIs.

You need:

- Python 3.9+
- Git
- Claude Code CLI
- OpenCode CLI
- working authentication/configuration for the model providers used by those CLIs

The default example configuration uses:

- Grok 4.7 via OpenCode with `high` reasoning
- DeepSeek via Claude Code with `max` reasoning

Model identifiers and executable locations are configurable.

## Configure

`loop-review` reads:

```text
~/.config/loop-review/config.toml
```

Start from the bundled example:

```text
skills/loop-review/assets/config.example.toml
```

Typical configuration:

```toml
version = 1

[paths]
run_root = "~/code/agents-tmp/loop-review"

[loop]
max_cycles = 2
max_model_calls = 6

[reviewers.grok]
adapter = "opencode"
executable = "opencode"
model = "openrouter/x-ai/grok-4.7"
reasoning = "high"
timeout_seconds = 3600

[reviewers.deepseek]
adapter = "claude"
executable = "claude"
model = "deepseek-flash[1m]"
reasoning = "max"
timeout_seconds = 1800
```

The reviewer timeouts are hard wall-clock ceilings, not target runtimes. Grok receives a larger ceiling because repository inspection can legitimately run for many tool steps even at `high` reasoning.

If a reviewer times out, `loop-review` fails closed but preserves partial `raw.stdout`, `raw.stderr`, `meta.json`, and `error.json` in that round directory for diagnosis. Blind-discovery workers are collected in completion order, so a peer result that finishes successfully is retained even if the other reviewer later fails.

Cross-check ledger updates are applied transactionally. New findings receive stable IDs first; `REFINE` and `DUPLICATE_OF` relations are resolved to canonical targets before any ledger mutation is committed. Duplicate/superseded chains are flattened, cycles are rejected deterministically, and adjudication array order does not change the resulting ledger. A relation failure leaves the previous ledger unchanged.

Use CLI/provider-native authentication. Do not put API keys or access tokens in this configuration file.

If `claude` or `opencode` is not on `PATH`, set `executable` to an explicit path such as `~/.local/bin/claude`.

## Verify installation

Resolve `SKILL_ROOT` to the installed `loop-review` Skill directory and run:

```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" doctor
```

The doctor command verifies both configured reviewer CLIs, models, reasoning levels, and structured-output compatibility.

## Usage

Ask an outer agent that can execute local commands to use `loop-review`.

Examples:

```text
Use loop-review to review the current working tree.
```

```text
Use loop-review to review this design for correctness, simplicity, and AGENTS.md compliance.
```

```text
Use loop-review to independently verify this existing review against the source repository.
```

Supported modes:

- `design`
- `code`
- `review`

Real reviews are detached by default, so outer agents do not need to predict whether a review will be short or long:

```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" run \
  --invocation /path/to/invocation.json
```

The command returns immediately with a durable `job_id`. Query that same review later without rerunning models:

```bash
python3 "$SKILL_ROOT/scripts/loop_review.py" status --job <job_id>
```

If the exact job id was lost, `status` without `--job` resolves the most recent detached job. `--dry-run` remains synchronous, and `--foreground` is available only for explicit debugging/manual synchronous execution. The older `--detach` flag remains accepted as a compatibility alias for the default behavior.

Detached execution deliberately uses no daemon, database, queue, or server: it is the same controller process started in a new OS session, with a small metadata/stdout/stderr record under `<run_root>/_jobs/`. If that local process itself disappears before producing a terminal result, status is `ORPHANED`; automatic crash-resume is intentionally out of scope.

The controller runs blind discovery first, then bounded serial cross-checks. The final controller state can be:

- `FROZEN_PASS`
- `FROZEN_CHANGES_REQUIRED`
- `FROZEN_DISPUTED`
- `UNRESOLVED_MAX_CYCLES`
- `FAILED`

A frozen controller result is **not** automatic acceptance. The outer agent separately reports:

- `ACCEPTED`
- `REJECTED`
- `NEEDS_FOLLOWUP`

See `skills/loop-review/references/outer-agent-acceptance.md` for the acceptance protocol.

## Security model

This repository intentionally contains **no API keys, access tokens, passwords, private keys, cookies, or provider credentials**.

The Skill:

- relies on existing Claude Code/OpenCode authentication;
- does not copy provider credentials into run artifacts;
- disables Skill delegation for reviewer workers;
- constrains reviewer workers to read/search capabilities;
- rejects target/repository drift during a review;
- fails closed instead of silently falling back to one reviewer.

The local configuration file `~/.config/loop-review/config.toml` is not part of this repository.

### Review artifacts may be sensitive

Review runs can contain:

- user requests;
- design documents;
- source diffs;
- file paths;
- prompts;
- raw model output;
- issue ledgers.

They are stored under the configured `run_root`. Treat that directory as potentially sensitive and do not commit it to source control.

## Development

Run the test suite:

```bash
python3 -m unittest discover -s skills/loop-review/tests -v
```

The current suite covers controller flow, read-only adapters, schema validation, `AGENTS.md` rule handling, issue-ledger transitions, failure auditing, and OpenCode/Claude adapter behavior.

## Updating

If installed with the Skills CLI:

```bash
npx skills check
npx skills update
```

## Status

The Skill is functional and has been exercised with real Claude Code and OpenCode reviewer calls. Before making this repository public, choose and add an explicit open-source license.
