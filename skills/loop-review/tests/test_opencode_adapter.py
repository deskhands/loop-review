import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.adapters.opencode import OpenCodeAdapter


class OpenCodeParserTests(unittest.TestCase):
    def setUp(self):
        self.adapter = OpenCodeAdapter("grok", {
            "executable": "/bin/false",
            "model": "x",
            "reasoning": "xhigh",
            "timeout_seconds": 1,
        })

    def test_last_text_event_is_final_json(self):
        events = [
            {"type": "text", "part": {"text": "I will inspect the target."}},
            {"type": "tool_use", "part": {"type": "tool"}},
            {"type": "text", "part": {"text": '{"ok":true}'}},
        ]
        stdout = "\n".join(json.dumps(x) for x in events)
        self.assertEqual(self.adapter._extract_json(stdout), {"ok": True})

    def test_strips_markdown_fence(self):
        event = {"type": "text", "part": {"text": "~~~json\n{\"ok\":true}\n~~~"}}
        self.assertEqual(self.adapter._extract_json(json.dumps(event)), {"ok": True})

    def test_trailing_narration_after_json_is_ignored(self):
        events = [
            {"type": "text", "part": {"text": '{"ok":true}'}},
            {"type": "text", "part": {"text": "Review complete."}},
        ]
        stdout = "\n".join(json.dumps(x) for x in events)
        self.assertEqual(self.adapter._extract_json(stdout), {"ok": True})

    def test_read_only_permission_contract(self):
        env = self.adapter._env()
        cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(cfg["permission"]["*"], "deny")
        self.assertEqual(cfg["permission"]["read"], "allow")
        self.assertEqual(cfg["permission"]["glob"], "allow")
        self.assertEqual(cfg["permission"]["grep"], "allow")
        self.assertEqual(env["OPENCODE_DISABLE_EXTERNAL_SKILLS"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"], "1")


if __name__ == "__main__":
    unittest.main()
