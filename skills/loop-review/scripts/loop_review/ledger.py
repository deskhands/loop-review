from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
    """Apply one cross-check result as an order-independent transaction.

    New findings receive stable IDs first. Adjudication relations are then
    resolved against the pre-commit batch graph, so a duplicate may point to a
    finding that is refined or deduplicated in the same result. The original
    ledger is mutated only after the full batch validates.
    """
    working = copy.deepcopy(ledger)
    local_map, added = _add_findings(working, result["findings"], reviewer, cycle)

    actions: Dict[str, Dict[str, Any]] = {}
    for adj in result["adjudications"]:
        fid = adj["finding_id"]
        if fid in actions:
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"Duplicate adjudication for finding {fid}",
                {"finding_id": fid},
            )
        if fid not in working["findings"]:
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"Adjudication references unknown finding {fid}",
                {"finding_id": fid},
            )
        if working["findings"][fid]["status"] not in ("OPEN", "DISPUTED"):
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"Adjudication references non-active finding {fid}",
                {"finding_id": fid, "status": working["findings"][fid]["status"]},
            )
        actions[fid] = adj

    def relation_target(fid: str) -> Optional[str]:
        adj = actions.get(fid)
        if adj is not None:
            if adj["decision"] == "REFINE":
                local = adj.get("replacement_local_id")
                if local not in local_map:
                    raise LoopReviewError(
                        "LEDGER_RELATION_INVALID",
                        f"REFINE {fid} replacement was not created",
                        {"finding_id": fid, "replacement_local_id": local},
                    )
                return local_map[local]
            if adj["decision"] == "DUPLICATE_OF":
                target = adj.get("duplicate_of")
                if not target:
                    raise LoopReviewError(
                        "LEDGER_RELATION_INVALID",
                        f"DUPLICATE_OF {fid} has no target",
                        {"finding_id": fid},
                    )
                return target

        item = working["findings"].get(fid)
        if item is None:
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"Relation target does not exist: {fid}",
                {"finding_id": fid},
            )
        if item["status"] == "SUPERSEDED":
            return item.get("relations", {}).get("superseded_by")
        if item["status"] == "DUPLICATE":
            return item.get("relations", {}).get("duplicate_of")
        return None

    def canonical_target(start: str) -> str:
        current = start
        seen: Set[str] = set()
        trail: List[str] = []
        while True:
            if current in seen:
                trail.append(current)
                raise LoopReviewError(
                    "LEDGER_RELATION_CYCLE",
                    f"Finding relation cycle detected: {' -> '.join(trail)}",
                    {"cycle": trail},
                )
            seen.add(current)
            trail.append(current)
            if current not in working["findings"]:
                raise LoopReviewError(
                    "LEDGER_RELATION_INVALID",
                    f"Relation target does not exist: {current}",
                    {"target": current, "trail": trail},
                )
            nxt = relation_target(current)
            if nxt is None:
                return current
            current = nxt

    duplicate_targets: Dict[str, str] = {}
    for fid, adj in actions.items():
        if adj["decision"] != "DUPLICATE_OF":
            continue
        raw_target = adj.get("duplicate_of")
        if not raw_target:
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"DUPLICATE_OF {fid} has no target",
                {"finding_id": fid},
            )
        canonical = canonical_target(raw_target)
        if canonical == fid:
            raise LoopReviewError(
                "LEDGER_RELATION_CYCLE",
                f"Finding {fid} resolves as a duplicate of itself",
                {"finding_id": fid, "target": raw_target},
            )
        duplicate_targets[fid] = canonical

    for fid in sorted(actions):
        adj = actions[fid]
        item = working["findings"][fid]
        decision = adj["decision"]
        if decision in ("ACCEPT", "REJECT"):
            item["positions"][reviewer] = {
                "decision": decision,
                "cycle": cycle,
                "rationale": adj["rationale"],
                "evidence": adj["evidence"],
            }
            _recompute(item)
        elif decision == "REFINE":
            replacement = local_map[adj["replacement_local_id"]]
            item["status"] = "SUPERSEDED"
            item["relations"]["superseded_by"] = replacement
        elif decision == "DUPLICATE_OF":
            item["status"] = "DUPLICATE"
            item["relations"]["duplicate_of"] = duplicate_targets[fid]
        else:
            raise LoopReviewError(
                "LEDGER_RELATION_INVALID",
                f"Unsupported adjudication decision: {decision}",
                {"finding_id": fid, "decision": decision},
            )

    working["history"].append({
        "cycle": cycle,
        "reviewer": reviewer,
        "new_findings": added,
        "adjudicated": sorted(actions),
    })

    ledger.clear()
    ledger.update(working)
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
