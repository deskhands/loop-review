from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from .util import LoopReviewError


EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "line_start": {"type": ["integer", "null"]},
        "line_end": {"type": ["integer", "null"]},
        "description": {"type": "string"},
    },
    "required": ["path", "line_start", "line_end", "description"],
    "additionalProperties": False,
}

RULE_REF_SCHEMA = {
    "type": "object",
    "description": "Reference to a binding rule from an applicable AGENTS.md only. Use evidence for ADRs, design docs, source files, tests, requirements, or other project documents.",
    "properties": {
        "path": {
            "type": "string",
            "description": "Exact path of one applicable AGENTS.md listed in the prompt; never an ADR, design document, source file, test, or requirement document.",
        },
        "description": {"type": "string"},
    },
    "required": ["path", "description"],
    "additionalProperties": False,
}

RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "summary": {"type": "string"},
        "policies_checked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "status": {"type": "string", "enum": ["CHECKED", "VIOLATION"]},
                    "violation_local_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "status", "violation_local_ids"],
                "additionalProperties": False,
            },
        },
        "adjudications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["ACCEPT", "REJECT", "REFINE", "DUPLICATE_OF"]},
                    "rationale": {"type": "string"},
                    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
                    "replacement_local_id": {"type": ["string", "null"]},
                    "duplicate_of": {"type": ["string", "null"]},
                },
                "required": ["finding_id", "decision", "rationale", "evidence", "replacement_local_id", "duplicate_of"],
                "additionalProperties": False,
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                    "blocking": {"type": "boolean"},
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
                    "rule_refs": {
                        "type": "array",
                        "description": "AGENTS.md policy references only. Must be [] when the finding is not an applicable AGENTS.md violation.",
                        "items": RULE_REF_SCHEMA,
                    },
                    "rationale": {"type": "string"},
                    "required_change": {"type": "string"},
                },
                "required": [
                    "local_id", "severity", "blocking", "category", "title", "claim",
                    "evidence", "rule_refs", "rationale", "required_change"
                ],
                "additionalProperties": False,
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "freeze_assessment": {
            "type": "object",
            "properties": {
                "can_freeze": {"type": "boolean"},
                "blocking_local_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["can_freeze", "blocking_local_ids"],
            "additionalProperties": False,
        },
    },
    "required": [
        "schema_version", "summary", "policies_checked", "adjudications",
        "findings", "open_questions", "freeze_assessment"
    ],
    "additionalProperties": False,
}


def _need(obj: Dict[str, Any], key: str, typ: Any, where: str) -> Any:
    if key not in obj:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Missing {where}.{key}")
    value = obj[key]
    if not isinstance(value, typ):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Invalid type for {where}.{key}")
    return value


def _validate_evidence(items: Any, where: str) -> None:
    if not isinstance(items, list):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{where} must be an array")
    for i, ev in enumerate(items):
        if not isinstance(ev, dict):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{where}[{i}] must be object")
        _need(ev, "path", str, f"{where}[{i}]")
        _need(ev, "description", str, f"{where}[{i}]")
        for k in ("line_start", "line_end"):
            if ev.get(k) is not None and not isinstance(ev.get(k), int):
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{where}[{i}].{k} must be integer or null")


def validate_result(
    obj: Any,
    expected_policy_paths: Iterable[str],
    active_finding_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Reviewer result must be a JSON object")
    if obj.get("schema_version") != "1.0":
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "schema_version must be 1.0")
    _need(obj, "summary", str, "result")
    policies = _need(obj, "policies_checked", list, "result")
    adjudications = _need(obj, "adjudications", list, "result")
    findings = _need(obj, "findings", list, "result")
    questions = _need(obj, "open_questions", list, "result")
    freeze = _need(obj, "freeze_assessment", dict, "result")
    if not all(isinstance(x, str) for x in questions):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "open_questions entries must be strings")
    _need(freeze, "can_freeze", bool, "freeze_assessment")
    blocking_ids = _need(freeze, "blocking_local_ids", list, "freeze_assessment")
    if not all(isinstance(x, str) for x in blocking_ids):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "blocking_local_ids entries must be strings")

    expected = {str(x) for x in expected_policy_paths}
    seen_policies: Set[str] = set()
    policy_by_path: Dict[str, Dict[str, Any]] = {}
    for i, p in enumerate(policies):
        if not isinstance(p, dict):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"policies_checked[{i}] must be object")
        path = _need(p, "path", str, f"policies_checked[{i}]")
        status = _need(p, "status", str, f"policies_checked[{i}]")
        if path not in expected:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Unexpected AGENTS.md path: {path}")
        if path in seen_policies:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Duplicate AGENTS.md acknowledgement: {path}")
        if status not in ("CHECKED", "VIOLATION"):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Invalid policy status: {status}")
        ids = _need(p, "violation_local_ids", list, f"policies_checked[{i}]")
        if not all(isinstance(x, str) for x in ids):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "violation_local_ids must be strings")
        if status == "CHECKED" and ids:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"CHECKED policy {path} cannot list violations")
        if status == "VIOLATION" and not ids:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"VIOLATION policy {path} must list finding ids")
        seen_policies.add(path)
        policy_by_path[path] = p
    missing = expected - seen_policies
    if missing:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Reviewer did not confirm AGENTS.md files: {sorted(missing)}")

    local_ids: Set[str] = set()
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"findings[{i}] must be object")
        lid = _need(f, "local_id", str, f"findings[{i}]")
        if lid in local_ids:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Duplicate local finding id: {lid}")
        local_ids.add(lid)
        sev = _need(f, "severity", str, f"findings[{i}]")
        if sev not in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Invalid severity: {sev}")
        _need(f, "blocking", bool, f"findings[{i}]")
        for k in ("category", "title", "claim", "rationale", "required_change"):
            _need(f, k, str, f"findings[{i}]")
        _validate_evidence(f.get("evidence"), f"findings[{i}].evidence")
        refs = f.get("rule_refs")
        if not isinstance(refs, list):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"findings[{i}].rule_refs must be array")
        for j, ref in enumerate(refs):
            if not isinstance(ref, dict):
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "rule_refs entries must be objects")
            rule_path = _need(ref, "path", str, f"rule_refs[{j}]")
            _need(ref, "description", str, f"rule_refs[{j}]")
            if rule_path not in expected:
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Finding {lid} references non-applicable AGENTS.md: {rule_path}")
    findings_by_id = {f["local_id"]: f for f in findings}
    for path, policy in policy_by_path.items():
        listed = set(policy["violation_local_ids"])
        if policy["status"] == "VIOLATION":
            for lid in listed:
                finding = findings_by_id.get(lid)
                if finding is None:
                    raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Policy {path} references unknown finding {lid}")
                if not finding["blocking"]:
                    raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Policy violation finding {lid} must be blocking")
                if path not in {ref["path"] for ref in finding["rule_refs"]}:
                    raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Finding {lid} does not reference violating policy {path}")

        for lid, finding in findings_by_id.items():
            if path in {ref["path"] for ref in finding["rule_refs"]}:
                if policy["status"] != "VIOLATION" or lid not in listed:
                    raise LoopReviewError(
                        "SCHEMA_VALIDATION_FAILED",
                        f"Finding {lid} cites {path} in rule_refs but that policy does not list the finding as a violation",
                    )
                if not finding["blocking"]:
                    raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"AGENTS.md violation finding {lid} must be blocking")
    active = set(active_finding_ids or [])
    seen_adj: Set[str] = set()
    for i, a in enumerate(adjudications):
        if not isinstance(a, dict):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"adjudications[{i}] must be object")
        fid = _need(a, "finding_id", str, f"adjudications[{i}]")
        if fid in seen_adj:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Duplicate adjudication for finding {fid}")
        if active_finding_ids is not None and fid not in active:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Adjudication references non-active finding {fid}")
        decision = _need(a, "decision", str, f"adjudications[{i}]")
        if decision not in ("ACCEPT", "REJECT", "REFINE", "DUPLICATE_OF"):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Invalid adjudication decision: {decision}")
        _need(a, "rationale", str, f"adjudications[{i}]")
        _validate_evidence(a.get("evidence"), f"adjudications[{i}].evidence")
        repl = a.get("replacement_local_id")
        dup = a.get("duplicate_of")
        if repl is not None and not isinstance(repl, str):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "replacement_local_id must be string or null")
        if dup is not None and not isinstance(dup, str):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "duplicate_of must be string or null")
        if decision == "REFINE" and (not repl or repl not in local_ids):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"REFINE {fid} must reference a new local finding")
        if decision == "DUPLICATE_OF" and not dup:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"DUPLICATE_OF {fid} needs duplicate_of")
        seen_adj.add(fid)

    if active_finding_ids is not None and seen_adj != active:
        missing_adj = active - seen_adj
        extra_adj = seen_adj - active
        raise LoopReviewError(
            "SCHEMA_VALIDATION_FAILED",
            f"Adjudications must match active findings exactly; missing={sorted(missing_adj)} extra={sorted(extra_adj)}",
        )

    return obj
