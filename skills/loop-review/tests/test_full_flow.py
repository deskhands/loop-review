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

    def parse_saved_output(self, stdout):
        return json.loads(stdout)

    def _success(self, out_dir):
        result = empty_result(self.policy_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw.stdout").write_text(json.dumps(result))
        (out_dir / "raw.stderr").write_text("")
        return {
            "result": result,
            "meta": {
                "status": "OK",
                "duration_ms": 1,
                "exit_code": 0,
                "model": self.name,
                "reasoning": "test",
                "timeout_seconds": 1,
            },
        }

    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.calls += 1
        return self._success(out_dir)


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


class FailOnCallAdapter(FakeAdapter):
    def __init__(self, name, policy_path, fail_on):
        super().__init__(name, policy_path)
        self.fail_on = fail_on

    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.calls += 1
        if self.calls != self.fail_on:
            return self._success(out_dir)
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
        }))
        raise LoopReviewError("TIMEOUT", "fake timeout", {"timeout_seconds": 1})


class RecoverableInvalidAdapter(FakeAdapter):
    def __init__(self, name, policy_path, fail_on):
        super().__init__(name, policy_path)
        self.fail_on = fail_on

    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.calls += 1
        if self.calls != self.fail_on:
            return self._success(out_dir)
        result = empty_result(self.policy_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw.stdout").write_text(json.dumps(result))
        (out_dir / "raw.stderr").write_text("")
        (out_dir / "meta.json").write_text(json.dumps({
            "status": "FAILED",
            "duration_ms": 1,
            "exit_code": 0,
            "model": self.name,
            "reasoning": "test",
            "timeout_seconds": 1,
        }))
        (out_dir / "error.json").write_text(json.dumps({
            "code": "INVALID_JSON",
            "message": "simulated parser failure",
        }))
        raise LoopReviewError("INVALID_JSON", "simulated parser failure")


def make_case(td):
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
        '[reviewers.glm]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "high"\ntimeout_seconds = 1\n'
        '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
    )
    invocation = {
        "schema_version": "1.0",
        "mode": "design",
        "repo": str(repo),
        "request": {"kind": "text", "content": "Review it."},
        "target": {"kind": "file", "path": str(target)},
    }
    inv = td / "invocation.json"
    inv.write_text(json.dumps(invocation))
    return repo, rule, target, cfg_path, inv


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
                '[reviewers.glm]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "glm": FakeAdapter("glm", str(rule.resolve())),
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
            self.assertEqual(controller.reviewers["glm"].calls, 2)
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
                '[reviewers.glm]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "high"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "glm": TimeoutAdapter("glm", str(rule.resolve())),
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
            self.assertTrue((run_dir / "rounds" / "00-discovery" / "glm" / "error.json").is_file())


    def test_resume_reuses_successful_discovery_and_reruns_only_missing_slots(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, _, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            deepseek = FakeAdapter("deepseek", str(rule.resolve()))
            controller.reviewers = {
                "glm": TimeoutAdapter("glm", str(rule.resolve())),
                "deepseek": deepseek,
            }

            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            run_dir = Path(caught.exception.details["run_dir"])
            saved_peer = (run_dir / "rounds" / "00-discovery" / "deepseek" / "result.json").read_text()

            resumed_glm = FakeAdapter("glm", str(rule.resolve()))
            controller.reviewers = {
                "glm": resumed_glm,
                "deepseek": deepseek,
            }
            result = controller.resume(run_dir)

            self.assertEqual(result["status"], "FROZEN_PASS")
            self.assertEqual(result["review_calls"], 5)
            self.assertEqual(result["resume"]["reused_results"], 1)
            self.assertEqual(result["resume"]["recovered_from_raw"], 0)
            self.assertEqual(result["resume"]["rerun_calls"], 3)
            self.assertEqual(
                (run_dir / "rounds" / "00-discovery" / "deepseek" / "result.json").read_text(),
                saved_peer,
            )
            self.assertEqual(resumed_glm.calls, 2)
            self.assertEqual(deepseek.calls, 2)

    def test_resume_crosscheck_failure_reruns_only_failed_slot(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, _, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            glm = FakeAdapter("glm", str(rule.resolve()))
            deepseek = FailOnCallAdapter("deepseek", str(rule.resolve()), fail_on=2)
            controller.reviewers = {"glm": glm, "deepseek": deepseek}

            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            self.assertEqual(caught.exception.code, "TIMEOUT")
            run_dir = Path(caught.exception.details["run_dir"])
            self.assertEqual(glm.calls, 2)
            self.assertEqual(deepseek.calls, 2)

            resumed_deepseek = FakeAdapter("deepseek", str(rule.resolve()))
            resumed_glm = FakeAdapter("glm", str(rule.resolve()))
            controller.reviewers = {
                "glm": resumed_glm,
                "deepseek": resumed_deepseek,
            }
            result = controller.resume(run_dir)

            self.assertEqual(result["status"], "FROZEN_PASS")
            self.assertEqual(result["review_calls"], 5)
            self.assertEqual(result["resume"]["reused_results"], 3)
            self.assertEqual(result["resume"]["recovered_from_raw"], 0)
            self.assertEqual(result["resume"]["rerun_calls"], 1)
            self.assertEqual(resumed_glm.calls, 0)
            self.assertEqual(resumed_deepseek.calls, 1)

    def test_resume_recovers_valid_raw_without_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, _, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            glm = RecoverableInvalidAdapter("glm", str(rule.resolve()), fail_on=2)
            deepseek = FakeAdapter("deepseek", str(rule.resolve()))
            controller.reviewers = {"glm": glm, "deepseek": deepseek}

            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            self.assertEqual(caught.exception.code, "INVALID_JSON")
            run_dir = Path(caught.exception.details["run_dir"])
            before_glm = glm.calls
            before_deepseek = deepseek.calls

            result = controller.resume(run_dir)

            self.assertEqual(result["status"], "FROZEN_PASS")
            self.assertEqual(result["review_calls"], 4)
            self.assertEqual(result["resume"]["reused_results"], 2)
            self.assertEqual(result["resume"]["recovered_from_raw"], 1)
            self.assertEqual(result["resume"]["rerun_calls"], 1)
            self.assertEqual(glm.calls, before_glm)
            self.assertEqual(deepseek.calls, before_deepseek + 1)
            recovered_meta = json.loads(
                (run_dir / "rounds" / "01-cross-check" / "glm" / "meta.json").read_text()
            )
            self.assertEqual(recovered_meta["status"], "RECOVERED_FROM_RAW")

    def test_resume_refuses_input_drift_without_model_calls(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, target, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            deepseek = FakeAdapter("deepseek", str(rule.resolve()))
            controller.reviewers = {
                "glm": TimeoutAdapter("glm", str(rule.resolve())),
                "deepseek": deepseek,
            }

            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            run_dir = Path(caught.exception.details["run_dir"])
            target.write_text("# Design\nChanged after failure.\n")
            resumed_glm = FakeAdapter("glm", str(rule.resolve()))
            controller.reviewers = {
                "glm": resumed_glm,
                "deepseek": deepseek,
            }

            with self.assertRaises(LoopReviewError) as resumed:
                controller.resume(run_dir)
            self.assertEqual(resumed.exception.code, "FAILED_INPUT_CHANGED")
            self.assertEqual(resumed_glm.calls, 0)
            self.assertEqual(deepseek.calls, 1)


    def test_resume_refuses_reviewer_config_change(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, _, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "glm": TimeoutAdapter("glm", str(rule.resolve())),
                "deepseek": FakeAdapter("deepseek", str(rule.resolve())),
            }
            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            run_dir = Path(caught.exception.details["run_dir"])

            changed = load_config(cfg_path)
            changed["reviewers"]["glm"]["model"] = "different-model"
            changed_controller = LoopReviewController(changed, SKILL)
            changed_controller.reviewers = {
                "glm": FakeAdapter("glm", str(rule.resolve())),
                "deepseek": FakeAdapter("deepseek", str(rule.resolve())),
            }
            with self.assertRaises(LoopReviewError) as resumed:
                changed_controller.resume(run_dir)
            self.assertEqual(resumed.exception.code, "RESUME_CONFIG_CHANGED")
            self.assertEqual(changed_controller.reviewers["glm"].calls, 0)
            self.assertEqual(changed_controller.reviewers["deepseek"].calls, 0)

    def test_resume_refuses_terminal_success(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, rule, _, cfg_path, inv = make_case(td)
            controller = LoopReviewController(load_config(cfg_path), SKILL)
            controller.reviewers = {
                "glm": FakeAdapter("glm", str(rule.resolve())),
                "deepseek": FakeAdapter("deepseek", str(rule.resolve())),
            }
            result = controller.run(inv)
            with self.assertRaises(LoopReviewError) as resumed:
                controller.resume(Path(result["run_dir"]))
            self.assertEqual(resumed.exception.code, "RESUME_NOT_SUPPORTED")


    def test_resume_supports_code_working_tree_target(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            repo, rule, target, cfg_path, inv = make_case(td)
            target.write_text("# Design\nChanged working tree.\n")
            inv.write_text(json.dumps({
                "schema_version": "1.0",
                "mode": "code",
                "repo": str(repo),
                "request": {"kind": "text", "content": "Review the working tree."},
                "target": {"kind": "working-tree"},
            }))

            controller = LoopReviewController(load_config(cfg_path), SKILL)
            deepseek = FakeAdapter("deepseek", str(rule.resolve()))
            controller.reviewers = {
                "glm": TimeoutAdapter("glm", str(rule.resolve())),
                "deepseek": deepseek,
            }
            with self.assertRaises(LoopReviewError) as caught:
                controller.run(inv)
            run_dir = Path(caught.exception.details["run_dir"])

            resumed_glm = FakeAdapter("glm", str(rule.resolve()))
            controller.reviewers = {
                "glm": resumed_glm,
                "deepseek": deepseek,
            }
            result = controller.resume(run_dir)

            self.assertEqual(result["status"], "FROZEN_PASS")
            self.assertEqual(result["resume"]["reused_results"], 1)
            self.assertEqual(result["resume"]["rerun_calls"], 3)
            self.assertEqual(resumed_glm.calls, 2)
            self.assertEqual(deepseek.calls, 2)


if __name__ == "__main__":
    unittest.main()
