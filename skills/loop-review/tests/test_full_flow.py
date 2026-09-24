import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

from loop_review.config import load_config
from loop_review.controller import LoopReviewController
from loop_review.util import LoopReviewError


def empty_result(policy_path):
    return {
        "schema_version": "1.0",
        "summary": "No material issues found.",
        "policies_checked": [{"path": policy_path, "status": "CHECKED", "violation_local_ids": []}],
        "adjudications": [],
        "findings": [],
        "open_questions": [],
        "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
    }


class FakeAdapter:
    def __init__(self, name, policy_path):
        self.name = name
        self.policy_path = policy_path
        self.calls = 0

    def healthcheck(self, cwd):
        return {"ok": True, "version": "fake", "model": self.name, "reasoning": "test"}

    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw.stdout").write_text('{"fake":true}\n')
        (out_dir / "raw.stderr").write_text("")
        return {
            "result": empty_result(self.policy_path),
            "meta": {
                "status": "OK",
                "duration_ms": 1,
                "exit_code": 0,
                "model": self.name,
                "reasoning": "test",
                "timeout_seconds": 1,
            },
        }


class TimeoutAdapter(FakeAdapter):
    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw.stdout").write_text("partial")
        (out_dir / "raw.stderr").write_text("")
        (out_dir / "meta.json").write_text(json.dumps({
            "status": "TIMEOUT",
            "duration_ms": 1000,
            "exit_code": None,
            "model": self.name,
            "reasoning": "test",
            "timeout_seconds": 1,
        }))
        (out_dir / "error.json").write_text(json.dumps({
            "code": "TIMEOUT",
            "message": "fake timeout",
            "timeout_seconds": 1,
        }))
        raise LoopReviewError("TIMEOUT", "fake timeout", {"timeout_seconds": 1})


class FullFlowTests(unittest.TestCase):
    def test_complete_design_pass_flow(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo = td / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            rule = repo / "AGENTS.md"
            rule.write_text("Keep it simple.\n")
            target = repo / "design.md"
            target.write_text("# Design\nUse the existing path.\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

            cfg_path = td / "config.toml"
            cfg_path.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "' + str(td / "runs") + '"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "grok": FakeAdapter("grok", str(rule.resolve())),
                "deepseek": FakeAdapter("deepseek", str(rule.resolve())),
            }

            invocation = {
                "schema_version": "1.0",
                "mode": "design",
                "repo": str(repo),
                "request": {"kind": "text", "content": "Review it."},
                "target": {"kind": "file", "path": str(target)},
            }
            inv = td / "invocation.json"
            inv.write_text(json.dumps(invocation))

            result = controller.run(inv)
            self.assertEqual(result["status"], "FROZEN_PASS")
            self.assertEqual(result["review_calls"], 4)
            self.assertEqual(result["cycles"], 1)
            self.assertTrue(Path(result["review_path"]).is_file())
            self.assertEqual(controller.reviewers["grok"].calls, 2)
            self.assertEqual(controller.reviewers["deepseek"].calls, 2)

    def test_discovery_failure_preserves_peer_result_and_counts_both_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo = td / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            rule = repo / "AGENTS.md"
            rule.write_text("Keep it simple.\n")
            target = repo / "design.md"
            target.write_text("# Design\nUse the existing path.\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

            cfg_path = td / "config.toml"
            cfg_path.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "' + str(td / "runs") + '"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "high"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "grok": TimeoutAdapter("grok", str(rule.resolve())),
                "deepseek": FakeAdapter("deepseek", str(rule.resolve())),
            }

            invocation = {
                "schema_version": "1.0",
                "mode": "design",
                "repo": str(repo),
                "request": {"kind": "text", "content": "Review it."},
                "target": {"kind": "file", "path": str(target)},
            }
            inv = td / "invocation.json"
            inv.write_text(json.dumps(invocation))

            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            self.assertEqual(caught.exception.code, "TIMEOUT")
            run_dir = Path(caught.exception.details["run_dir"])
            state = json.loads((run_dir / "state" / "state.json").read_text())
            self.assertEqual(state["review_calls"], 2)
            self.assertTrue((run_dir / "rounds" / "00-discovery" / "deepseek" / "result.json").is_file())
            self.assertTrue((run_dir / "rounds" / "00-discovery" / "deepseek" / "meta.json").is_file())
            self.assertTrue((run_dir / "rounds" / "00-discovery" / "grok" / "error.json").is_file())


if __name__ == "__main__":
    unittest.main()
