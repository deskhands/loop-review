from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .util import LoopReviewError


def object_schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
EVIDENCE = object_schema({
    "path": TEXT, "line_start": {"type": ["integer", "null"]},
    "line_end": {"type": ["integer", "null"]}, "description": TEXT,
})
FINDING = object_schema({
    "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
    "blocking": {"type": "boolean"}, "category": TEXT, "title": TEXT,
    "claim": TEXT, "evidence": {"type": "array", "items": EVIDENCE},
    "rule_refs": {"type": "array", "items": object_schema({"path": TEXT, "description": TEXT})},
    "rationale": TEXT, "required_change": TEXT,
})
DECISION = object_schema({
    "finding_id": TEXT,
    "decision": {"type": "string", "enum": ["ACCEPT", "REJECT", "UNCERTAIN"]},
    "rationale": TEXT, "evidence": {"type": "array", "items": EVIDENCE},
})
RESULT_SCHEMA = object_schema({
    "schema_version": {"type": "string", "const": "2.0"},
    "summary": TEXT, "policies_checked": {"type": "array", "items": TEXT},
    "findings": {"type": "array", "items": FINDING},
    "adjudications": {"type": "array", "items": DECISION},
    "open_questions": {"type": "array", "items": TEXT},
})


def _validate(value: Any, schema: Dict[str, Any], location: str) -> None:
    kinds = schema["type"]
    kinds = kinds if isinstance(kinds, list) else [kinds]
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "null": type(None)}
    if not any(type(value) is types[kind] for kind in kinds):
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{location}: expected {kinds}")
    if "const" in schema and value != schema["const"]:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{location}: wrong protocol version")
    if "enum" in schema and value not in schema["enum"]:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{location}: invalid value {value!r}")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) != set(properties):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"{location}: unexpected or missing fields")
        for key, child in properties.items():
            _validate(value[key], child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{location}[{index}]")


def validate_result(
    obj: Any, expected_policy_paths: Iterable[str],
    active_finding_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    _validate(obj, RESULT_SCHEMA, "result")
    expected = set(expected_policy_paths)
    policies = obj["policies_checked"]
    if len(policies) != len(set(policies)) or set(policies) != expected:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Confirm every applicable AGENTS.md exactly once")
    for finding in obj["findings"]:
        if not finding["title"].strip() or not finding["claim"].strip() or not finding["evidence"]:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Findings require a claim and source evidence")
        for ref in finding["rule_refs"]:
            if ref["path"] not in expected or not finding["blocking"]:
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Rule violations must cite an applicable policy and block")
    seen = [item["finding_id"] for item in obj["adjudications"]]
    required = set(active_finding_ids or [])
    if len(seen) != len(set(seen)) or set(seen) != required:
        raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Adjudications must match supplied claim IDs exactly")
    for item in obj["adjudications"]:
        if not item["rationale"].strip() or (item["decision"] != "UNCERTAIN" and not item["evidence"]):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Verification decisions need rationale and source evidence")
    evidence_items = [ev for f in obj["findings"] for ev in f["evidence"]]
    evidence_items += [ev for a in obj["adjudications"] for ev in a["evidence"]]
    for evidence in evidence_items:
        start, end = evidence["line_start"], evidence["line_end"]
        if not evidence["path"].strip() or not evidence["description"].strip():
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Evidence needs a path and description")
        if (start is not None and start < 1) or (end is not None and (start is None or end < start)):
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", "Evidence line range is invalid")
    return obj
