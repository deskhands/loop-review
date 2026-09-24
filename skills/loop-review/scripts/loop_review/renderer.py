from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _evidence_lines(evidence: Iterable[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for ev in evidence:
        loc = ev["path"]
        if ev.get("line_start") is not None:
            loc += f":{ev['line_start']}"
            if ev.get("line_end") not in (None, ev.get("line_start")):
                loc += f"-{ev['line_end']}"
        out.append(f"- `{loc}` — {ev['description']}")
    return out or ["- No line-specific evidence supplied."]


def render_round(result: Dict[str, Any], stable_map: Dict[str, str]) -> str:
    lines = ["# Review Result", "", "## Summary", "", result["summary"], "", "## Mandatory Policy Compliance", ""]
    if result["policies_checked"]:
        for p in result["policies_checked"]:
            lines.append(f"- **{p['status']}** `{p['path']}`")
    else:
        lines.append("- No applicable AGENTS.md files.")
    lines += ["", "## Findings", ""]
    if not result["findings"]:
        lines.append("No new findings.")
    for f in result["findings"]:
        sid = stable_map.get(f["local_id"], f["local_id"])
        lines += [
            f"### {sid} — [{f['severity']}] {f['title']}",
            "",
            f"Blocking: **{'yes' if f['blocking'] else 'no'}**  ",
            f"Category: `{f['category']}`",
            "",
            "#### Finding", "",
            f["claim"], "",
            "#### Evidence", "",
            *_evidence_lines(f["evidence"]),
            "",
        ]
        if f["rule_refs"]:
            lines += ["#### AGENTS.md rule", ""]
            for ref in f["rule_refs"]:
                lines.append(f"- `{ref['path']}` — {ref['description']}")
            lines.append("")
        lines += ["#### Why this matters", "", f["rationale"], "", "#### Required change", "", f["required_change"], ""]
    lines += ["## Existing Finding Adjudication", ""]
    if not result["adjudications"]:
        lines.append("None.")
    for a in result["adjudications"]:
        extra = ""
        if a["decision"] == "REFINE":
            extra = f" → {stable_map.get(a.get('replacement_local_id') or '', a.get('replacement_local_id'))}"
        elif a["decision"] == "DUPLICATE_OF":
            extra = f" → {a.get('duplicate_of')}"
        lines += [f"- **{a['finding_id']} — {a['decision']}**{extra}: {a['rationale']}"]
    lines += ["", "## Open Questions", ""]
    lines += [f"- {q}" for q in result["open_questions"]] or ["None."]
    lines += [
        "",
        "## Freeze Assessment",
        "",
        f"Reviewer assessment: **{'can freeze' if result['freeze_assessment']['can_freeze'] else 'cannot freeze'}**",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_final(status: str, ledger: Dict[str, Any], summaries: List[str]) -> str:
    accepted = [(fid, f) for fid, f in ledger["findings"].items() if f["status"] == "ACCEPTED"]
    disputed = [(fid, f) for fid, f in ledger["findings"].items() if f["status"] == "DISPUTED"]
    open_items = [(fid, f) for fid, f in ledger["findings"].items() if f["status"] == "OPEN"]
    lines = [
        "# Final Loop Review",
        "",
        f"**Status: {status}**",
        "",
        "## Summary",
        "",
    ]
    if summaries:
        lines.append(summaries[-1])
    else:
        lines.append("Review completed.")
    lines += ["", "## Accepted Findings", ""]
    if not accepted:
        lines.append("None.")
    for fid, item in accepted:
        f = item["finding"]
        lines += [
            f"### {fid} — [{f['severity']}] {f['title']}",
            "",
            f"Blocking: **{'yes' if f['blocking'] else 'no'}**",
            "",
            f["claim"],
            "",
            "Evidence:",
            *_evidence_lines(f["evidence"]),
            "",
            f"Required change: {f['required_change']}",
            "",
        ]
    lines += ["## Disputed Findings", ""]
    if not disputed:
        lines.append("None.")
    for fid, item in disputed:
        lines += [f"- **{fid}** — {item['finding']['title']}"]
        for reviewer, pos in sorted(item["positions"].items()):
            lines.append(f"  - {reviewer}: {pos['decision']} — {pos.get('rationale','')}")
    lines += ["", "## Unresolved Open Findings", ""]
    if not open_items:
        lines.append("None.")
    for fid, item in open_items:
        lines.append(f"- **{fid}** — {item['finding']['title']}")
    lines += ["", "## Freeze Meaning", ""]
    if status == "FROZEN_PASS":
        lines.append("The review conclusion is frozen with no accepted blocking findings.")
    elif status == "FROZEN_CHANGES_REQUIRED":
        lines.append("The review conclusion is frozen; the reviewed target still requires changes.")
    elif status == "FROZEN_DISPUTED":
        lines.append("The review budget ended with unresolved reviewer disagreement.")
    elif status == "UNRESOLVED_MAX_CYCLES":
        lines.append("The review budget ended before the finding set stabilized.")
    else:
        lines.append("The review did not complete successfully.")
    return "\n".join(lines).rstrip() + "\n"
