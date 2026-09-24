import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.config import load_config
from loop_review.controller import LoopReviewController
from loop_review.schemas import validate_result
from loop_review.util import LoopReviewError


class HardeningTests(unittest.TestCase):
    def test_bare_executable_names_remain_path_resolvable(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.toml"
            p.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "/tmp/loop-review-tests"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "opencode"\nmodel = "x"\nreasoning = "high"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "claude"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            cfg = load_config(p)
            self.assertEqual(cfg["reviewers"]["grok"]["executable"], "opencode")
            self.assertEqual(cfg["reviewers"]["deepseek"]["executable"], "claude")

    def test_config_rejects_adapter_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.toml"
            p.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "/tmp/loop-review-tests"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            with self.assertRaises(LoopReviewError):
                load_config(p)

    def test_rejects_non_active_adjudication(self):
        obj = {
            "schema_version": "1.0",
            "summary": "x",
            "policies_checked": [],
            "adjudications": [{
                "finding_id": "F999",
                "decision": "REJECT",
                "rationale": "x",
                "evidence": [],
                "replacement_local_id": None,
                "duplicate_of": None,
            }],
            "findings": [],
            "open_questions": [],
            "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
        }
        with self.assertRaises(LoopReviewError):
            validate_result(obj, [], ["F001"])

    def test_nonblocking_finding_may_reference_checked_policy(self):
        policy = "/repo/AGENTS.md"
        obj = {
            "schema_version": "1.0",
            "summary": "x",
            "policies_checked": [{"path": policy, "status": "CHECKED", "violation_local_ids": []}],
            "adjudications": [],
            "findings": [{
                "local_id": "L1",
                "severity": "LOW",
                "blocking": False,
                "category": "validation",
                "title": "Tighten validation wording",
                "claim": "x",
                "evidence": [],
                "rule_refs": [{"path": policy, "description": "validation should be stated"}],
                "rationale": "x",
                "required_change": "x",
            }],
            "open_questions": [],
            "freeze_assessment": {"can_freeze": True, "blocking_local_ids": []},
        }
        validate_result(obj, [policy])

    def test_policy_violation_must_link_to_blocking_finding(self):
        policy = "/repo/AGENTS.md"
        obj = {
            "schema_version": "1.0",
            "summary": "x",
            "policies_checked": [{
                "path": policy,
                "status": "VIOLATION",
                "violation_local_ids": ["L1"],
            }],
            "adjudications": [],
            "findings": [{
                "local_id": "L1",
                "severity": "HIGH",
                "blocking": True,
                "category": "policy",
                "title": "Rule violation",
                "claim": "x",
                "evidence": [],
                "rule_refs": [{"path": policy, "description": "keep simple"}],
                "rationale": "x",
                "required_change": "x",
            }],
            "open_questions": [],
            "freeze_assessment": {"can_freeze": False, "blocking_local_ids": ["L1"]},
        }
        validate_result(obj, [policy])
        obj["findings"][0]["rule_refs"][0]["path"] = "/other/AGENTS.md"
        with self.assertRaises(LoopReviewError):
            validate_result(obj, [policy])

    def test_external_target_parent_is_added_to_read_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo = td / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("x\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

            external = td / "external"
            external.mkdir()
            target = external / "design.md"
            target.write_text("# Design\n")
            cfg_path = td / "config.toml"
            cfg_path.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "' + str(td / "runs") + '"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), Path(__file__).resolve().parents[1])
            run_id, run_dir = controller._new_run_dir(repo, "design")
            inv = {
                "schema_version": "1.0",
                "mode": "design",
                "repo": str(repo),
                "request": {"kind": "text", "content": "review"},
                "target": {"kind": "file", "path": str(target)},
            }
            prepared = controller._prepare(inv, run_dir, repo)
            self.assertEqual(prepared["read_dirs"], [external.resolve()])

    def test_run_root_inside_repo_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo = td / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            cfg_path = td / "config.toml"
            cfg_path.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "' + str(repo / "runs") + '"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), Path(__file__).resolve().parents[1])
            with self.assertRaises(LoopReviewError):
                controller._new_run_dir(repo, "design")

    def test_unexpected_error_persists_failure_audit_and_run_dir(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo = td / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("x\n")
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
            controller = LoopReviewController(load_config(cfg_path), Path(__file__).resolve().parents[1])
            controller._prepare = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            invocation = {
                "schema_version": "1.0",
                "mode": "design",
                "repo": str(repo),
                "request": {"kind": "text", "content": "review"},
                "target": {"kind": "text", "content": "# Design"},
            }
            inv = td / "invocation.json"
            inv.write_text(json.dumps(invocation))
            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            self.assertEqual(caught.exception.code, "UNEXPECTED_ERROR")
            run_dir = Path(caught.exception.details["run_dir"])
            status = json.loads((run_dir / "final" / "status.json").read_text())
            self.assertEqual(status["failure_code"], "UNEXPECTED_ERROR")


if __name__ == "__main__":
    unittest.main()
