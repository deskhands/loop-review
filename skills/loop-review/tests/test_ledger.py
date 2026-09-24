import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.ledger import active_ids, add_discovery_result, apply_crosscheck_result, new_ledger
from loop_review.util import LoopReviewError


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


def adjudication(finding_id, decision, *, replacement_local_id=None, duplicate_of=None):
    return {
        "finding_id": finding_id,
        "decision": decision,
        "rationale": "checked",
        "evidence": [],
        "replacement_local_id": replacement_local_id,
        "duplicate_of": duplicate_of,
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


    def test_refine_and_duplicate_to_refined_target_canonicalize(self):
        ledger = new_ledger()
        add_discovery_result(
            ledger,
            {"findings": [finding("R1", "Original"), finding("R2", "Duplicate")]},
            "Reviewer-A",
        )
        result = {
            "findings": [finding("N1", "Refined")],
            "adjudications": [
                adjudication("F001", "REFINE", replacement_local_id="N1"),
                adjudication("F002", "DUPLICATE_OF", duplicate_of="F001"),
            ],
        }
        local_map, _ = apply_crosscheck_result(ledger, result, "Reviewer-B", 1)
        replacement = local_map["N1"]
        self.assertEqual(replacement, "F003")
        self.assertEqual(ledger["findings"]["F001"]["status"], "SUPERSEDED")
        self.assertEqual(ledger["findings"]["F001"]["relations"]["superseded_by"], "F003")
        self.assertEqual(ledger["findings"]["F002"]["status"], "DUPLICATE")
        self.assertEqual(ledger["findings"]["F002"]["relations"]["duplicate_of"], "F003")
        self.assertEqual(ledger["findings"]["F003"]["status"], "OPEN")

    def test_adjudication_order_does_not_change_ledger(self):
        base = new_ledger()
        add_discovery_result(
            base,
            {"findings": [finding("R1", "Original"), finding("R2", "Duplicate")]},
            "Reviewer-A",
        )
        a = {
            "findings": [finding("N1", "Refined")],
            "adjudications": [
                adjudication("F001", "REFINE", replacement_local_id="N1"),
                adjudication("F002", "DUPLICATE_OF", duplicate_of="F001"),
            ],
        }
        b = {
            "findings": [finding("N1", "Refined")],
            "adjudications": list(reversed(a["adjudications"])),
        }
        ledger_a = copy.deepcopy(base)
        ledger_b = copy.deepcopy(base)
        apply_crosscheck_result(ledger_a, a, "Reviewer-B", 1)
        apply_crosscheck_result(ledger_b, b, "Reviewer-B", 1)
        self.assertEqual(ledger_a, ledger_b)

    def test_duplicate_chain_is_flattened_to_canonical_root(self):
        ledger = new_ledger()
        add_discovery_result(
            ledger,
            {"findings": [
                finding("R1", "Root"),
                finding("R2", "Duplicate one"),
                finding("R3", "Duplicate two"),
            ]},
            "Reviewer-A",
        )
        result = {
            "findings": [],
            "adjudications": [
                adjudication("F001", "ACCEPT"),
                adjudication("F002", "DUPLICATE_OF", duplicate_of="F001"),
                adjudication("F003", "DUPLICATE_OF", duplicate_of="F002"),
            ],
        }
        apply_crosscheck_result(ledger, result, "Reviewer-B", 1)
        self.assertEqual(ledger["findings"]["F002"]["relations"]["duplicate_of"], "F001")
        self.assertEqual(ledger["findings"]["F003"]["relations"]["duplicate_of"], "F001")

    def test_relation_cycle_rolls_back_transaction(self):
        ledger = new_ledger()
        add_discovery_result(
            ledger,
            {"findings": [finding("R1", "One"), finding("R2", "Two")]},
            "Reviewer-A",
        )
        before = copy.deepcopy(ledger)
        result = {
            "findings": [],
            "adjudications": [
                adjudication("F001", "DUPLICATE_OF", duplicate_of="F002"),
                adjudication("F002", "DUPLICATE_OF", duplicate_of="F001"),
            ],
        }
        with self.assertRaises(LoopReviewError) as caught:
            apply_crosscheck_result(ledger, result, "Reviewer-B", 1)
        self.assertEqual(caught.exception.code, "LEDGER_RELATION_CYCLE")
        self.assertEqual(ledger, before)


if __name__ == "__main__":
    unittest.main()
