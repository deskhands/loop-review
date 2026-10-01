from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from .util import LoopReviewError, load_json, sha256_file, unique_preserve, write_text

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


def report_context(directory: Path) -> Dict[str, Any]:
    """Read persisted inputs and intact completed checkpoints, never raw events."""
    context: Dict[str, Any] = {'summaries': {}, 'warnings': []}
    for name, path in [('invocation', directory / 'input/invocation.json'),
                       ('manifest', directory / 'manifest.json')]:
        try:
            value = load_json(path)
            if isinstance(value, dict):
                context[name] = value
        except (LoopReviewError, OSError):
            pass  # Preparation can fail before these files exist.
    request = directory / 'input/request.md'
    if request.is_file():
        context['request'] = request.read_text(encoding='utf-8')
    for checkpoint in sorted((directory / 'audit').glob('*/*/checkpoint.json')):
        slot = checkpoint.parent
        try:
            saved = load_json(checkpoint)
            attempt = saved['attempt']
            if not isinstance(attempt, str) or not attempt.startswith('attempt-') or Path(attempt).name != attempt:
                raise ValueError('Invalid attempt path')
            result_path = slot / attempt / 'result.json'
            result_path.resolve().relative_to(slot.resolve())
            if sha256_file(result_path) != saved['result_sha256']:
                raise ValueError('Changed result checksum')
            output = load_json(result_path)
            context['summaries'][f'{slot.parent.name}/{slot.name}'] = output['summary']
        except (LoopReviewError, OSError, ValueError, KeyError, TypeError):
            context['warnings'].append(f'{slot.parent.name}/{slot.name}: checkpoint summary unavailable or invalid.')
    return context


def _evidence(lines: list[str], evidence: list[Dict[str, Any]], repo: str) -> None:
    for item in evidence:
        path = Path(item['path'])
        if not path.is_absolute():
            path = Path(repo) / path
        location = f"{path}:{item['line_start']}" if item.get('line_start') else str(path)
        lines.append(f"- [{location}]({quote(location, safe='/:' )}): {item['description']}")


def render_final(state: Dict[str, Any], result: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    context = context or {}
    findings = result.get('findings', [])
    questions = unique_preserve(result.get('open_questions', []))
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
    lines += ['## Review recommendation', '']
    if state['status'] in ('FAILED', 'CANCELED', 'ORPHANED'):
        lines += ['Execution is incomplete. Use the available evidence to guide follow-up; no passing conclusion is established.', '']
    elif state['status'] in ('INIT', 'PREPARED', 'PREPARED_DRY_RUN', 'RESUMING', 'DISCOVERY', 'VERIFICATION'):
        lines += ['This is a progress document. Final review recommendations are not yet available.', '']
    else:
        blocking = [item['id'] for item in findings if item['status'] == 'ACCEPTED' and item['finding']['blocking']]
        if blocking:
            lines += [f"Address confirmed blocking issues {', '.join(blocking)} before accepting the reviewed target.", '']
        else:
            lines += ['No confirmed blocking issue is recorded within the reviewed scope.', '']
        if questions or any(item['status'] in ('DISPUTED', 'UNVERIFIED') for item in findings):
            lines += ['The review process has ended with unresolved evidence or scope questions. '
                      'Assess the specific items below before accepting the target; completion does not establish full coverage.', '']
        lines += ['Reviewer agreement is supporting evidence, not proof. The final acceptance decision belongs to the user or receiving agent.', '']
    counts = {status: sum(item['status'] == status for item in findings)
              for status in ('ACCEPTED', 'DISPUTED', 'UNVERIFIED', 'RESOLVED')}
    lines += [f"Confirmed: {counts['ACCEPTED']} · Contested: {counts['DISPUTED']} · "
              f"Unverified: {counts['UNVERIFIED']} · Resolved historical: {counts['RESOLVED']}", '',
              '## Request and review scope', '', context.get('request', 'Original request unavailable in this document.'), '']
    invocation = context.get('invocation', {})
    target = invocation.get('target', {})
    paths = target.get('paths', []) if target.get('kind') == 'files' else [target['path']] if target.get('path') else []
    for path in paths:
        target_path = Path(path)
        if not target_path.is_absolute():
            target_path = Path(state['repo']) / target_path
        lines.append(f'- Target: `{target_path}`')
    if target.get('kind') in ('git-range', 'working-tree'):
        lines.append(f"- Target: {target['kind']} {target.get('base', '')} → {target.get('head', 'working tree')}")
    if target.get('kind') == 'text':
        lines += ['Target supplied as text:', '', target.get('content', ''), '']
    manifest = context.get('manifest', {})
    if manifest.get('repo', {}).get('head'):
        lines.append(f"- Repository HEAD at preparation: `{manifest['repo']['head']}` (input may also include working-tree changes).")
    for reviewer, config in manifest.get('reviewers', {}).items():
        lines.append(f"- Reviewer {reviewer}: `{config.get('model', 'unknown')}` via `{config.get('adapter', 'unknown')}`.")
    lines += ['', '## Coverage and reviewer summaries', '',
              'Scope is defined by the request and targets above. Successful execution alone does not prove every requested file was reviewed.', '']
    for slot, summary in context.get('summaries', {}).items():
        lines += [f'### {slot}', '', summary, '']
    if not context.get('summaries'):
        lines += ['No completed reviewer summary is available.', '']
    lines += [f'- {warning}' for warning in context.get('warnings', [])]
    groups = [('ACCEPTED', 'Confirmed issues'), ('DISPUTED', 'Contested / counter-evidence'),
              ('UNVERIFIED', 'Unverified issues'), ('RESOLVED', 'Resolved historical issues')]
    for status, heading in groups:
        lines += [f'## {heading}', '']
        items = [item for item in findings if item['status'] == status]
        if not items:
            lines += ['None recorded.', '']
        for item in items:
            finding = item['finding']
            lines += [f"### {item['id']} — {finding['title']}", '',
                      f"Severity: {finding['severity']} · Blocking: {finding['blocking']} · Origin: {item['origin']}", '',
                      f"Claim: {finding['claim']}", '', f"Impact / rationale: {finding.get('rationale', '')}", '',
                      'Original evidence:', '']
            _evidence(lines, finding['evidence'], state['repo'])
            if finding.get('rule_refs'):
                lines += ['', 'Applicable project rules:', '']
                _evidence(lines, finding['rule_refs'], state['repo'])
            lines += ['', f"Original proposed change: {finding['required_change']}", '']
            for reviewer, position in sorted(item.get('positions', {}).items()):
                lines += [f"Cross-check by {reviewer}: **{position['decision']}** — {position['rationale']}", '']
                _evidence(lines, position.get('evidence', []), state['repo'])
                lines.append('')
            action = {'ACCEPTED': f"Evaluate and apply the confirmed recommendation: {finding['required_change']}",
                      'DISPUTED': 'Do not apply the original requested change without checking the counter-evidence.',
                      'UNVERIFIED': 'Check the cited source and establish whether the claim is valid before changing the target.',
                      'RESOLVED': 'The historical claim is resolved in this review; no further change is requested for this claim.'}[status]
            lines += [f'Next action: {action}', '']
            if item.get('previous_id'):
                lines += [f"Historical claim: {item['previous_id']}. RESOLVED requires source evidence from both reviewers.", '']
    lines += ['## Open questions and follow-up', '',
              'These questions record remaining uncertainty. They are not automatically blocking defects; '
              'discovery questions may be answered by the counter-evidence above.', '']
    lines += [f'- {question}' for question in questions] or ['None recorded.']
    return '\n'.join(lines).rstrip() + '\n'


def render_execution(state: Dict[str, Any]) -> str:
    lines = ['# Execution audit', '', f"Run: `{state['run_id']}` · Status: `{state['status']}`", '',
             f"Task attempts: {state['review_calls']} · Active time: {state['elapsed_seconds']:.1f}s", '']
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
              'Raw events, checkpoints and immutable attempts are retained alongside this execution audit.']
    return "\n".join(lines).rstrip() + "\n"


def publish_reports(directory: Path, state: Dict[str, Any], result: Dict[str, Any]) -> None:
    state['report_path'] = str(directory / 'final-review.md')
    document = render_final(state, result, report_context(directory))
    write_text(directory / 'final-review.md', document)
    write_text(directory / 'report.md', document)  # Compatibility copy, one source of content.
    write_text(directory / 'audit/execution.md', render_execution(state))
