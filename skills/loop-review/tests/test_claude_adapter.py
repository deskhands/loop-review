import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.adapters.claude import ClaudeAdapter
from loop_review.util import LoopReviewError


class ClaudeAdapterTests(unittest.TestCase):
    def test_review_adds_external_read_dirs_and_read_only_tools(self):
        adapter = ClaudeAdapter("deepseek", {
            "executable": "/fake/claude",
            "model": "deepseek-flash[1m]",
            "reasoning": "max",
            "timeout_seconds": 10,
        })
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            result = {
                "schema_version": "1.0",
                "summary": "ok",
                "policies_checked": [],
                "adjudications": [],
                "findings": [],
                "open_questions": [],
                "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
            }
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"is_error": False, "structured_output": result}),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            external = td / "external"
            external.mkdir()
            with patch("loop_review.adapters.claude.run_cmd", side_effect=fake_run):
                adapter.review("prompt", td, td / "run", out, [external])

        argv = captured["argv"]
        self.assertIn("--allowedTools", argv)
        self.assertIn("Read,Glob,Grep", argv)
        pairs = list(zip(argv, argv[1:]))
        self.assertIn(("--add-dir", str(external)), pairs)

    def test_timeout_persists_partial_output_and_error_artifacts(self):
        adapter = ClaudeAdapter("deepseek", {
            "executable": "/fake/claude",
            "model": "deepseek-flash[1m]",
            "reasoning": "max",
            "timeout_seconds": 2,
        })
        error = LoopReviewError(
            "TIMEOUT",
            "timed out",
            {"stdout": "partial-json", "stderr": "partial-error", "timeout_seconds": 2},
        )
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            with patch("loop_review.adapters.claude.run_cmd", side_effect=error):
                with self.assertRaises(LoopReviewError):
                    adapter.review("prompt", td, td / "run", out)

            self.assertEqual((out / "raw.stdout").read_text(), "partial-json")
            self.assertEqual((out / "raw.stderr").read_text(), "partial-error")
            meta = json.loads((out / "meta.json").read_text())
            err = json.loads((out / "error.json").read_text())
            self.assertEqual(meta["status"], "TIMEOUT")
            self.assertEqual(meta["timeout_seconds"], 2)
            self.assertEqual(err["code"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
