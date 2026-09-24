from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .util import LoopReviewError


def new_ledger() -> Dict[str, Any]:
    return {"schema_version": "1.0", "next_id": 1, "findings": {}, "history": []}


def _next_id(ledger: Dict[str, Any]) -> str:
    fid = f"F{int(ledger['next_id']):03d}"
    ledger["next_id"] = int(ledger["next_id"]) + 1
    return fid


def _recompute(item: Dict[str, Any]) -> None:
    if item["status"] in ("SUPERSEDED", "DUPLICATE"):
        return
    if len(item["positions"]) < 2:
        item["status"] = "OPEN"
        return
    decisions = {pos["decision"] for pos in item["positions"].values()}
    if decisions == {"ACCEPT"}:
        item["status"] = "ACCEPTED"
    elif decisions == {"REJECT"}:
        item["status"] = "REJECTED"
    else:
        item["status"] = "DISPUTED"


def _add_findings(
    ledger: Dict[str, Any],
    findings: Iterable[Dict[str, Any]],
    reviewer: str,
    cycle: int,
) -> Tuple[Dict[str, str], List[str]]:
    local_map: Dict[str, str] = {}
    added: List[str] = []
    for finding in findings:
        fid = _next_id(ledger)
        local_map[finding["local_id"]] = fid
        ledger["findings"][fid] = {
            "status": "OPEN",
            "origin": reviewer,
            "origin_cycle": cycle,
            "finding": finding,
            "positions": {
                reviewer: {
                    "decision": "ACCEPT",
                    "cycle": cycle,
                    "rationale": "Origin reviewer reported this finding.",
                    "evidence": finding.get("evidence", []),
                }
            },
            "relations": {},
        }
        added.append(fid)
    return local_map, added


def add_discovery_result(ledger: Dict[str, Any], result: Dict[str, Any], reviewer: str) -> Dict[str, str]:
    local_map, added = _add_findings(ledger, result["findings"], reviewer, 0)
    ledger["history"].append({"cycle": 0, "reviewer": reviewer, "new_findings": added, "adjudicated": []})
    return local_map


def active_ids(ledger: Dict[str, Any]) -> List[str]:
    return sorted(fid for fid, item in ledger["findings"].items() if item["status"] in ("OPEN", "DISPUTED"))


def apply_crosscheck_result(
    ledger: Dict[str, Any],
    result: Dict[str, Any],
    reviewer: str,
    cycle: int,
) -> Tuple[Dict[str, str], List[str]]:
    local_map, added = _add_findings(ledger, result["findings"], reviewer, cycle)
    adjudicated: List[str] = []
    for adj in result["adjudications"]:
        fid = adj["finding_id"]
        if fid not in ledger["findings"]:
            raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Adjudication references unknown finding {fid}")
        item = ledger["findings"][fid]
        decision = adj["decision"]
        adjudicated.append(fid)
        if decision in ("ACCEPT", "REJECT"):
            item["positions"][reviewer] = {
                "decision": decision,
                "cycle": cycle,
                "rationale": adj["rationale"],
                "evidence": adj["evidence"],
            }
            _recompute(item)
        elif decision == "REFINE":
            local = adj.get("replacement_local_id")
            if local not in local_map:
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"REFINE {fid} replacement was not created")
            item["status"] = "SUPERSEDED"
            item["relations"]["superseded_by"] = local_map[local]
        elif decision == "DUPLICATE_OF":
            target = adj.get("duplicate_of")
            if target not in ledger["findings"]:
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"DUPLICATE_OF target does not exist: {target}")
            if target == fid:
                raise LoopReviewError("SCHEMA_VALIDATION_FAILED", f"Finding {fid} cannot duplicate itself")
            target_item = ledger["findings"][target]
            if target_item["status"] in ("DUPLICATE", "SUPERSEDED"):
                raise LoopReviewError(
                    "SCHEMA_VALIDATION_FAILED",
                    f"DUPLICATE_OF target {target} is not a live finding",
                )
            item["status"] = "DUPLICATE"
            item["relations"]["duplicate_of"] = target
    ledger["history"].append({"cycle": cycle, "reviewer": reviewer, "new_findings": added, "adjudicated": adjudicated})
    return local_map, added


def status_counts(ledger: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in ledger["findings"].values():
        out[item["status"]] = out.get(item["status"], 0) + 1
    return out


def accepted_blocking_ids(ledger: Dict[str, Any]) -> List[str]:
    return sorted(
        fid for fid, item in ledger["findings"].items()
        if item["status"] == "ACCEPTED" and item["finding"].get("blocking") is True
    )


def final_status(ledger: Dict[str, Any], converged: bool, last_cycle_had_new: bool) -> str:
    active = active_ids(ledger)
    disputed = [fid for fid in active if ledger["findings"][fid]["status"] == "DISPUTED"]
    opened = [fid for fid in active if ledger["findings"][fid]["status"] == "OPEN"]
    if converged:
        return "FROZEN_CHANGES_REQUIRED" if accepted_blocking_ids(ledger) else "FROZEN_PASS"
    if disputed:
        return "FROZEN_DISPUTED"
    if opened or last_cycle_had_new:
        return "UNRESOLVED_MAX_CYCLES"
    return "UNRESOLVED_MAX_CYCLES"
