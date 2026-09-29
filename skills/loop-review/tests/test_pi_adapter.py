import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.adapters.pi import PiAdapter
from loop_review.util import LoopReviewError


def event_stream(result, *, repeated=False):
    lines = [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": "/repo/AGENTS.md", "offset": 1, "limit": 200},
        },
    ]
    if repeated:
        lines.append({
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": "/repo/AGENTS.md", "offset": 1, "limit": 200},
        })
    lines.append({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "openrouter",
            "model": "z-ai/glm-5.3-flash",
            "content": [{"type": "text", "text": json.dumps(result)}],
            "usage": {
                "input": 100,
                "output": 20,
                "cacheRead": 40,
                "cacheWrite": 0,
                "reasoning": 10,
                "totalTokens": 170,
                "cost": {"total": 0.01},
            },
        },
    })
    lines.append({"type": "agent_end"})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


class PiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PiAdapter("glm", {
            "executable": "/fake/pi-glm",
            "model": "z-ai/glm-5.3-flash",
            "reasoning": "high",
            "timeout_seconds": 10,
        })

    def test_review_uses_openrouter_and_read_only_pi_tools(self):
        captured = {}
        result = {
            "schema_version": "1.0",
            "summary": "ok",
            "policies_checked": [],
            "adjudications": [],
            "findings": [],
            "open_questions": [],
            "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
        }

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return SimpleNamespace(
                returncode=0,
                stdout=event_stream(result),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            with patch("loop_review.adapters.pi.run_cmd", side_effect=fake_run):
                response = self.adapter.review("prompt", td, td / "run", out)

        argv = captured["argv"]
        pairs = list(zip(argv, argv[1:]))
        self.assertIn(("--provider", "openrouter"), pairs)
        self.assertIn(("--model", "z-ai/glm-5.3-flash"), pairs)
        self.assertIn(("--thinking", "high"), pairs)
        self.assertIn(("--tools", "read,grep,find,ls"), pairs)
        self.assertNotIn("bash", argv)
        self.assertNotIn("write", argv)
        self.assertNotIn("edit", argv)
        self.assertEqual(response["result"], result)
        self.assertEqual(response["meta"]["turns"], 1)
        self.assertEqual(response["meta"]["tool_calls"], 1)
        self.assertEqual(response["meta"]["max_identical_tool_calls"], 1)
        self.assertAlmostEqual(response["meta"]["usage"]["cost"], 0.01)

    def test_parser_reports_repeated_identical_tool_calls(self):
        result = {"ok": True}
        parsed, stats = self.adapter._extract(event_stream(result, repeated=True))
        self.assertEqual(parsed, result)
        self.assertEqual(stats["tool_calls"], 2)
        self.assertEqual(stats["max_identical_tool_calls"], 2)

    def test_timeout_persists_partial_event_stats(self):
        partial = event_stream({"ok": True}, repeated=True)
        error = LoopReviewError(
            "TIMEOUT",
            "timed out",
            {"stdout": partial, "stderr": "partial-error", "timeout_seconds": 2},
        )
        adapter = PiAdapter("glm", {
            "executable": "/fake/pi-glm",
            "model": "z-ai/glm-5.3-flash",
            "reasoning": "high",
            "timeout_seconds": 2,
        })
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            with patch("loop_review.adapters.pi.run_cmd", side_effect=error):
                with self.assertRaises(LoopReviewError):
                    adapter.review("prompt", td, td / "run", out)

            self.assertEqual((out / "raw.stdout").read_text(), partial)
            self.assertEqual((out / "raw.stderr").read_text(), "partial-error")
            meta = json.loads((out / "meta.json").read_text())
            self.assertEqual(meta["status"], "TIMEOUT")
            self.assertEqual(meta["tool_calls"], 2)
            self.assertEqual(meta["max_identical_tool_calls"], 2)
            self.assertAlmostEqual(meta["usage"]["cost"], 0.01)


if __name__ == "__main__":
    unittest.main()
