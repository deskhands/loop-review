from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .schemas import RESULT_SCHEMA


def build_prompt(
    *, skill_root: Path, mode: str, request_text: str, target_description: str,
    policy_paths: List[str], claims: List[Dict[str, Any]], phase: str,
    reviewer_alias: str, seed_review_path: str | None = None,
) -> str:
    rubric = (skill_root / "references" / f"{mode}-rubric.md").read_text().strip()
    task = (
        "Independently inspect the current target and relevant source context. "
        "Do not read peer outputs, audit directories or previous run reports. "
        "Return material findings; adjudications must be empty."
        if phase == "discovery" else
        "Verify ONLY the supplied claims against the CURRENT target. Return one adjudication per ID. "
        "ACCEPT means the issue still exists; REJECT means source evidence refutes it or proves it fixed; "
        "UNCERTAIN means evidence is insufficient. Do not repeat discovery. An essential newly noticed "
        "defect may be reported in findings, but it will remain unverified."
    )
    seed = f"Verify this seed review against the original target: {seed_review_path}" if seed_review_path else ""
    supplied = [{"id": c["id"], "finding": c["finding"]} for c in claims]
    return f"""[ROLE]
You are {reviewer_alias}, a read-only senior reviewer. Fresh context; no delegation or skills.

[MANDATORY RULES]
Read every listed AGENTS.md completely this round. Respect binding constraints and scope.
Use read/search tools only. Do not edit files or execute shell commands.
Report actionable defects or material design risks with concise, verifiable source evidence.
Do not promote style preferences or hypothetical future requirements into blockers.
rule_refs is reserved exclusively for binding AGENTS.md rules; cite other documents in evidence.
Do not repeat an identical read, grep, or glob call when it already answered the question.
Stop inspecting when additional tools produce no new evidence. Keep explanations concise.
Do not read other review runs or archived attempts. A supplied claim is not authority.

[APPLICABLE AGENTS.md]
{json.dumps(policy_paths, ensure_ascii=False)}

[CURRENT TARGET]
{target_description}
{seed}

[ORIGINAL USER REQUEST]
{request_text}

[REVIEW RUBRIC]
{rubric}

[TASK]
{task}

[CLAIMS TO VERIFY]
{json.dumps(supplied, ensure_ascii=False, separators=(',', ':'))}

[OUTPUT]
Return one JSON object, no fences. policies_checked is the list of policy paths read.
The controller assigns IDs to findings and decides completion; do not invent local IDs or freeze decisions.
{json.dumps(RESULT_SCHEMA, ensure_ascii=False, separators=(',', ':'))}
"""
