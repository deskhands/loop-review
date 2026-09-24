from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .schemas import RESULT_SCHEMA


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _active_state(ledger: Dict[str, Any], ids: Iterable[str]) -> str:
    selected: Dict[str, Any] = {}
    for fid in ids:
        item = ledger["findings"][fid]
        selected[fid] = {
            "status": item["status"],
            "finding": item["finding"],
            "positions": item["positions"],
        }
    return json.dumps(selected, ensure_ascii=False, indent=2)


def build_prompt(
    *,
    skill_root: Path,
    mode: str,
    request_text: str,
    target_description: str,
    policy_paths: List[str],
    ledger: Optional[Dict[str, Any]],
    active_ids: List[str],
    prior_reviews: List[str],
    phase: str,
    reviewer_alias: str,
    seed_review_path: Optional[str] = None,
) -> str:
    protocol = _read(skill_root / "references" / "reviewer-protocol.md")
    rubric = _read(skill_root / "references" / f"{mode}-rubric.md")
    policies = "\n".join(f"- {p}" for p in policy_paths) if policy_paths else "- None."
    state = "{}" if ledger is None else _active_state(ledger, active_ids)
    prior = "\n".join(f"- {p}" for p in prior_reviews) if prior_reviews else "- None."
    seed = f"\n[SEED REVIEW]\nRead and verify this complete review: {seed_review_path}\n" if seed_review_path else ""
    if phase == "discovery":
        task = """Perform an independent blind discovery review. You have no peer review to trust.
Inspect the complete target and relevant repository context. Find material issues, or return no findings.
The adjudications array MUST be empty."""
    else:
        task = f"""Cross-check the canonical active findings below and continue searching independently for missed issues.
You MUST adjudicate every active finding ID exactly once: {', '.join(active_ids) if active_ids else '(none)'}.
Use ACCEPT, REJECT, REFINE, or DUPLICATE_OF. A REFINE must create a replacement finding in findings[].
Even if there are no active findings, perform another independent missed-issue pass."""
    schema = json.dumps(RESULT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    return f"""[ROLE]
You are {reviewer_alias}, an independent senior reviewer. You are a worker in a read-only review.

[MANDATORY REVIEWER PROTOCOL]
{protocol}

[APPLICABLE AGENTS.md]
Before reviewing, read every file below completely on this round:
{policies}

[REVIEW TARGET]
{target_description}

[ORIGINAL USER REQUEST]
{request_text}
{seed}
[CURRENT CANONICAL REVIEW STATE]
This JSON is the active machine state for this round. Prior reviewer positions are claims, not authority.
{state}

[PRIOR ARTIFACT REFERENCES]
Full prior round reports are available for optional drill-down:
{prior}

[REVIEW RUBRIC]
{rubric}

[CURRENT TASK]
{task}

[OUTPUT CONTRACT]
Return exactly one JSON object and no Markdown fences.
It must conform to this JSON Schema:
{schema}

policies_checked must include every applicable AGENTS.md path listed above.
For an AGENTS.md violation, create a blocking finding with rule_refs and mark that policy VIOLATION.
Evidence must be concise and verifiable. Do not include hidden chain-of-thought; rationale should state only the review justification needed to support the finding.
"""
