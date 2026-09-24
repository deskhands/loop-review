import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.util import LoopReviewError, run_cmd


class UtilTests(unittest.TestCase):
    def test_timeout_preserves_partial_stdout_and_stderr(self):
        expired = subprocess.TimeoutExpired(
            cmd=["fake"],
            timeout=3,
            output=b"partial-out",
            stderr=b"partial-err",
        )
        with patch("loop_review.util.subprocess.run", side_effect=expired):
            with self.assertRaises(LoopReviewError) as caught:
                run_cmd(["fake"], timeout=3)

        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertEqual(caught.exception.details["stdout"], "partial-out")
        self.assertEqual(caught.exception.details["stderr"], "partial-err")
        self.assertEqual(caught.exception.details["timeout_seconds"], 3)


if __name__ == "__main__":
    unittest.main()
