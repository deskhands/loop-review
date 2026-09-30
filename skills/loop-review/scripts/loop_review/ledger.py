from __future__ import annotations

from typing import Any, Dict, List


def discovery_claims(results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    claims = []
    for reviewer in sorted(results):
        for finding in results[reviewer]["findings"]:
            claims.append({"id": f"F{len(claims) + 1:03d}", "origin": reviewer, "finding": finding})
    return claims


def resolve_claims(
    claims: List[Dict[str, Any]], verifications: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    decisions = {reviewer: {a["finding_id"]: a for a in result["adjudications"]}
                 for reviewer, result in verifications.items()}
    resolved = []
    for claim in claims:
        positions = {reviewer: items[claim["id"]] for reviewer, items in decisions.items()
                     if claim["id"] in items}
        votes = [position["decision"] for position in positions.values()]
        expected = 2 if claim["origin"] == "history" else 1
        if len(votes) != expected or "UNCERTAIN" in votes:
            status = "UNVERIFIED"
        elif all(vote == "ACCEPT" for vote in votes):
            status = "ACCEPTED"
        elif claim["origin"] == "history" and all(vote == "REJECT" for vote in votes):
            status = "RESOLVED"
        else:
            status = "DISPUTED"
        resolved.append({**claim, "status": status, "positions": positions})
    for reviewer, result in sorted(verifications.items()):
        for finding in result["findings"]:
            resolved.append({"id": f"N{len(resolved) + 1:03d}", "origin": reviewer,
                             "finding": finding, "status": "UNVERIFIED", "positions": {}})
    return resolved


def final_status(findings: List[Dict[str, Any]], questions: List[str], scope: str) -> str:
    if any(item["status"] == "UNVERIFIED" for item in findings) or questions:
        return "UNRESOLVED_MAX_CYCLES"  # Compatibility name; v2 has no cycles.
    if any(item["status"] == "DISPUTED" for item in findings):
        return "FROZEN_DISPUTED"
    if any(item["status"] == "ACCEPTED" and item["finding"]["blocking"] for item in findings):
        return "FROZEN_CHANGES_REQUIRED"
    return "FIXES_VERIFIED" if scope == "fixes" else "FROZEN_PASS"
