import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.schemas import validate_result
from loop_review.util import LoopReviewError


def base_result():
    return {
        "schema_version": "1.0",
        "summary": "ok",
        "policies_checked": [{"path": "/r/AGENTS.md", "status": "CHECKED", "violation_local_ids": []}],
        "adjudications": [],
        "findings": [],
        "open_questions": [],
        "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
    }


class SchemaTests(unittest.TestCase):
    def test_requires_policy_ack(self):
        validate_result(base_result(), ["/r/AGENTS.md"])
        bad = base_result()
        bad["policies_checked"] = []
        with self.assertRaises(LoopReviewError):
            validate_result(bad, ["/r/AGENTS.md"])

    def test_crosscheck_requires_all_active_adjudications(self):
        with self.assertRaises(LoopReviewError):
            validate_result(base_result(), ["/r/AGENTS.md"], ["F001"])


if __name__ == "__main__":
    unittest.main()
