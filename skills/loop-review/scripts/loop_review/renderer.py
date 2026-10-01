from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

LABELS = {
    "INIT": "Preparing", "PREPARED": "Ready", "PREPARED_DRY_RUN": "Prepared (no model calls)",
    "RESUMING": "Running: restoring checkpoints",
    "DISCOVERY": "Running: independent discovery", "VERIFICATION": "Running: targeted verification",
    "FROZEN_PASS": "Completed: no confirmed blocking findings",
    "FROZEN_CHANGES_REQUIRED": "Completed: changes required",
    "FROZEN_DISPUTED": "Completed: reviewer disagreement",
    "UNRESOLVED_MAX_CYCLES": "Completed: unverified issues or open questions",
    "FIXES_VERIFIED": "Completed: specified fixes verified (limited scope)",
    "FAILED": "Incomplete: execution failed", "CANCELED": "Incomplete: canceled",
    "ORPHANED": "Incomplete: controller process disappeared",
}


def render_final(state: Dict[str, Any], result: Dict[str, Any]) -> str:
    lines = [f"# {state['title']}", "", f"**{LABELS.get(state['status'], state['status'])}**", "",
             f"Run: `{state['run_id']}` · Task: `{state['task_id']}` · Mode: `{state['mode']}`",
             f"Scope: **{state['scope']}** · Updated: {state['updated_at']}", "",
             f"Repository: `{state['repo']}`", ""]
    if state['scope'] == 'fixes':
        lines += ["This verifies specified historical claims only. It is not a full review of the current target.", ""]
    if state.get('previous_run_id'):
        lines += [f"Previous run: `{state['previous_run_id']}`. Historical claims were withheld from full-review discovery.", ""]
    if state.get('failure_code'):
        lines += [f"Failure: **{state['failure_code']}** — {state.get('message', '')}", "",
                  "Available findings below are partial evidence, not a passing review.", ""]
    lines += ["## Execution", "", f"Task attempts: {state['review_calls']} · Active time: {state['elapsed_seconds']:.1f}s", ""]
    for key, budget in sorted(state.get('budgets', {}).items()):
        if budget['token_limit']:
            known = budget['known_tokens'] if budget['known_tokens'] is not None else 'unknown'
            lines.append(f"- {key} budget: {known} / {budget['token_limit']} known tokens; "
                         f"remaining allowance {budget['remaining_tokens']}; action **{budget['action']}**.")
    for key, progress in sorted(state.get('progress', {}).items()):
        tokens = progress.get('total_tokens')
        cost = progress.get('reported_cost')
        lines.append(f"- {key}: {progress.get('status', 'RUNNING')}; turns {progress.get('turns', 0)}, "
                     f"tools {progress.get('tool_calls', 0)}, tokens {tokens if tokens is not None else 'unknown'}, "
                     f"CLI-reported cost {cost if cost is not None else 'unknown'}.")
        breakdown = ', '.join(f"{label} {progress.get(field) if progress.get(field) is not None else 'unknown'}"
                              for field, label in [('input_tokens', 'uncached input'), ('cache_read_tokens', 'cache read'),
                                                   ('cache_write_tokens', 'cache write'), ('output_tokens', 'output')])
        lines.append(f"  Token breakdown: {breakdown}.")
        if progress.get('cli_reported_turns') is not None:
            lines.append(f"  CLI summary turns {progress['cli_reported_turns']} (separate counter; limit uses observed assistant calls).")
    lines += ["", "CLI cost estimates may not match provider billing. Cached tokens are included in known token totals.", "",
              "## Findings", ""]
    if not result.get('findings'):
        lines.append("No findings recorded." if state['status'] in ('FAILED', 'CANCELED') else "No material findings reported.")
    for item in result.get('findings', []):
        finding = item['finding']
        title = finding['title']
        lines += [f"### {item['id']} — {item['status']} — {title}", "",
                  f"Severity: {finding['severity']} · Blocking: {finding['blocking']}", "", finding['claim'], ""]
        for evidence in finding['evidence']:
            path = evidence['path']
            if not Path(path).is_absolute():
                path = str(Path(state['repo']) / path)
            location = f"{path}:{evidence['line_start']}" if evidence['line_start'] else path
            lines.append(f"- [{location}]({quote(location, safe='/:' )}): {evidence['description']}")
        lines += ["", f"Required change: {finding['required_change']}", ""]
        for reviewer, position in sorted(item.get('positions', {}).items()):
            lines.append(f"- {reviewer}: {position['decision']} — {position['rationale']}")
        if item.get('previous_id'):
            lines.append(f"- Historical claim: {item['previous_id']}. RESOLVED requires source evidence from both reviewers.")
        lines.append("")
    lines += ["## Open questions", ""]
    lines += [f"- {question}" for question in result.get('open_questions', [])] or ["None recorded."]
    lines += ["", "## Audit", "", "[Machine result](result.json) · [Current status](status.json) · [Manifest](manifest.json)", "",
              "Raw events and immutable attempts are under `audit/`. Reviewer agreement does not prove correctness; "
              "the outer agent must check material conclusions against the original request."]
    return "\n".join(lines).rstrip() + "\n"
