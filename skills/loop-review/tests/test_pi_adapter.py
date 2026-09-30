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


def provider_failure_stream():
    lines = [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "openrouter",
                "model": "qwen/qwen3.8-flash",
                "stopReason": "error",
                "errorMessage": "terminated",
                "content": [{"type": "text", "text": '{"schema_version":"1.0","summary":"partial'}],
                "usage": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "reasoning": 0,
                    "totalTokens": 0,
                    "cost": {"total": 0},
                },
            },
        },
        {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000, "errorMessage": "terminated"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "openrouter",
                "model": "qwen/qwen3.8-flash",
                "stopReason": "error",
                "errorMessage": "Request timed out.",
                "content": [],
                "usage": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "reasoning": 0,
                    "totalTokens": 0,
                    "cost": {"total": 0},
                },
            },
        },
        {"type": "auto_retry_end", "success": False, "attempt": 3, "finalError": "Request timed out."},
        {"type": "agent_settled"},
    ]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def recovered_retry_stream(result):
    lines = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "openrouter",
                "model": "qwen/qwen3.8-flash",
                "stopReason": "error",
                "errorMessage": "Connection error.",
                "content": [],
                "usage": {"cost": {"total": 0}},
            },
        },
        {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000, "errorMessage": "Connection error."},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "openrouter",
                "model": "qwen/qwen3.8-flash",
                "stopReason": "stop",
                "content": [{"type": "text", "text": json.dumps(result)}],
                "usage": {"cost": {"total": 0.01}},
            },
        },
        {"type": "auto_retry_end", "success": True, "attempt": 1},
        {"type": "agent_settled"},
    ]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


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
            "model": "qwen/qwen3.8-flash",
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
        self.adapter = PiAdapter("qwen", {
            "executable": "/fake/pi-openrouter",
            "model": "qwen/qwen3.8-flash",
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
            with patch("loop_review.adapters.pi.stream_review", side_effect=fake_run):
                response = self.adapter.review("prompt", td, td / "run", out)

        argv = captured["argv"]
        pairs = list(zip(argv, argv[1:]))
        self.assertIn(("--provider", "openrouter"), pairs)
        self.assertIn(("--model", "qwen/qwen3.8-flash"), pairs)
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

    def test_parser_accepts_terminal_json_after_narration(self):
        result = {"ok": True}
        raw = event_stream(result)
        lines = raw.splitlines()
        event = json.loads(lines[-2])
        text = event["message"]["content"][0]["text"]
        event["message"]["content"][0]["text"] = "Verified evidence. Returning result.\n\n" + text
        lines[-2] = json.dumps(event)
        parsed, _ = self.adapter._extract("\n".join(lines) + "\n")
        self.assertEqual(parsed, result)

    def test_parser_does_not_repair_malformed_terminal_json(self):
        result = {"ok": True}
        raw = event_stream(result)
        lines = raw.splitlines()
        event = json.loads(lines[-2])
        event["message"]["content"][0]["text"] = 'Narration with {braces}.\n{"ok":true'
        lines[-2] = json.dumps(event)
        with self.assertRaises(LoopReviewError):
            self.adapter._extract("\n".join(lines) + "\n")

    def test_parser_reports_retry_exhaustion_as_provider_failure(self):
        with self.assertRaises(LoopReviewError) as caught:
            self.adapter._extract(provider_failure_stream())
        self.assertEqual(caught.exception.code, "PROVIDER_REQUEST_FAILED")
        stats = caught.exception.details["stats"]
        self.assertTrue(stats["retry_exhausted"])
        self.assertEqual(stats["retry_final_error"], "Request timed out.")
        self.assertEqual(stats["last_assistant_stop_reason"], "error")
        self.assertEqual(stats["last_assistant_error"], "Request timed out.")
        self.assertEqual(stats["provider_errors"], ["terminated", "Request timed out."])

    def test_parser_accepts_success_after_transient_provider_error(self):
        result = {"ok": True}
        parsed, stats = self.adapter._extract(recovered_retry_stream(result))
        self.assertEqual(parsed, result)
        self.assertFalse(stats["retry_exhausted"])
        self.assertEqual(stats["last_assistant_stop_reason"], "stop")
        self.assertEqual(stats["provider_errors"], ["Connection error."])

    def test_review_exit_zero_with_retry_exhaustion_is_provider_failure(self):
        def fake_run(argv, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=provider_failure_stream(),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            with patch("loop_review.adapters.pi.stream_review", side_effect=fake_run):
                with self.assertRaises(LoopReviewError) as caught:
                    self.adapter.review("prompt", td, td / "run", out)

            self.assertEqual(caught.exception.code, "PROVIDER_REQUEST_FAILED")
            meta = json.loads((out / "meta.json").read_text())
            error = json.loads((out / "error.json").read_text())
            self.assertEqual(meta["status"], "FAILED")
            self.assertTrue(meta["retry_exhausted"])
            self.assertEqual(meta["retry_final_error"], "Request timed out.")
            self.assertEqual(error["code"], "PROVIDER_REQUEST_FAILED")

    def test_timeout_persists_partial_event_stats(self):
        partial = event_stream({"ok": True}, repeated=True)
        error = LoopReviewError(
            "TIMEOUT",
            "timed out",
            {"stdout": partial, "stderr": "partial-error", "timeout_seconds": 2},
        )
        adapter = PiAdapter("qwen", {
            "executable": "/fake/pi-openrouter",
            "model": "qwen/qwen3.8-flash",
            "reasoning": "high",
            "timeout_seconds": 2,
        })
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            with patch("loop_review.adapters.pi.stream_review", side_effect=error):
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
