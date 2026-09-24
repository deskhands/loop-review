import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.ledger import active_ids, add_discovery_result, apply_crosscheck_result, new_ledger


def finding(local_id, title="Issue", blocking=True):
    return {
        "local_id": local_id,
        "severity": "HIGH",
        "blocking": blocking,
        "category": "correctness",
        "title": title,
        "claim": "claim",
        "evidence": [{"path": "x.py", "line_start": 1, "line_end": 1, "description": "evidence"}],
        "rule_refs": [],
        "rationale": "reason",
        "required_change": "fix",
    }


class LedgerTests(unittest.TestCase):
    def test_two_reviewer_acceptance(self):
        ledger = new_ledger()
        r = {"findings": [finding("R1")]}
        m = add_discovery_result(ledger, r, "Reviewer-A")
        self.assertEqual(m["R1"], "F001")
        self.assertEqual(active_ids(ledger), ["F001"])
        result = {
            "findings": [],
            "adjudications": [{
                "finding_id": "F001",
                "decision": "ACCEPT",
                "rationale": "confirmed",
                "evidence": [],
                "replacement_local_id": None,
                "duplicate_of": None,
            }],
        }
        apply_crosscheck_result(ledger, result, "Reviewer-B", 1)
        self.assertEqual(ledger["findings"]["F001"]["status"], "ACCEPTED")
        self.assertEqual(active_ids(ledger), [])

    def test_disagreement_is_disputed(self):
        ledger = new_ledger()
        add_discovery_result(ledger, {"findings": [finding("R1")]}, "Reviewer-A")
        result = {
            "findings": [],
            "adjudications": [{
                "finding_id": "F001",
                "decision": "REJECT",
                "rationale": "not reproduced",
                "evidence": [],
                "replacement_local_id": None,
                "duplicate_of": None,
            }],
        }
        apply_crosscheck_result(ledger, result, "Reviewer-B", 1)
        self.assertEqual(ledger["findings"]["F001"]["status"], "DISPUTED")


if __name__ == "__main__":
    unittest.main()
