import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "scripts" / "loop_review.py"
SPEC = importlib.util.spec_from_file_location("loop_review_cli", CLI_PATH)
CLI = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CLI)


class DetachedCliTests(unittest.TestCase):
    def test_orphaned_job_is_detected_without_resume(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = {"paths": {"run_root": str(td / "runs")}}
            jobs = CLI._jobs_dir(cfg)
            job_id = "deadbeef"
            stdout_path = jobs / f"{job_id}.stdout"
            stderr_path = jobs / f"{job_id}.stderr"
            stdout_path.write_text("")
            stderr_path.write_text("")
            CLI.atomic_json(jobs / f"{job_id}.json", {
                "job_id": job_id,
                "pid": 99999999,
                "started_at": "2026-09-24T00:00:00+00:00",
                "invocation": str(td / "invocation.json"),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })
            status = CLI._detached_status(cfg, job_id)
            self.assertEqual(status["status"], "ORPHANED")

    def test_finished_job_returns_controller_result(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = {"paths": {"run_root": str(td / "runs")}}
            jobs = CLI._jobs_dir(cfg)
            job_id = "cafebabe"
            stdout_path = jobs / f"{job_id}.stdout"
            stderr_path = jobs / f"{job_id}.stderr"
            stdout_path.write_text(json.dumps({
                "status": "FROZEN_PASS",
                "run_id": "abc123",
                "run_dir": "/tmp/run",
            }))
            stderr_path.write_text("")
            CLI.atomic_json(jobs / f"{job_id}.json", {
                "job_id": job_id,
                "pid": 99999999,
                "started_at": "2026-09-24T00:00:00+00:00",
                "invocation": str(td / "invocation.json"),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })
            status = CLI._detached_status(cfg, job_id)
            self.assertEqual(status["status"], "FROZEN_PASS")
            self.assertEqual(status["job_id"], job_id)

    def _make_repo_and_invocation(self, td):
        repo = td / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        (repo / "AGENTS.md").write_text("Keep it simple.\n")
        target = repo / "design.md"
        target.write_text("# Design\nChange one word.\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

        inv = td / "invocation.json"
        inv.write_text(json.dumps({
            "schema_version": "1.0",
            "mode": "design",
            "repo": str(repo),
            "request": {"kind": "text", "content": "Review it."},
            "target": {"kind": "file", "path": str(target)},
        }))
        return repo, inv

    def _write_config(self, td, grok_executable="opencode", deepseek_executable="claude"):
        run_root = td / "runs"
        cfg = td / "config.toml"
        cfg.write_text(
            'version = 1\n'
            '[paths]\nrun_root = "' + str(run_root) + '"\n'
            '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
            '[reviewers.grok]\nadapter = "opencode"\nexecutable = "' + grok_executable + '"\nmodel = "x"\nreasoning = "high"\ntimeout_seconds = 1\n'
            '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "' + deepseek_executable + '"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
        )
        return cfg

    def test_dry_run_stays_foreground_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, inv = self._make_repo_and_invocation(td)
            cfg = self._write_config(td)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "--config",
                    str(cfg),
                    "run",
                    "--invocation",
                    str(inv),
                    "--dry-run",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "PREPARED_DRY_RUN")
            self.assertTrue(Path(result["run_dir"]).is_dir())
            self.assertFalse((td / "runs" / "_jobs").exists())

    def test_real_run_is_detached_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _, inv = self._make_repo_and_invocation(td)
            cfg = self._write_config(td, "/bin/false", "/bin/false")

            launched = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "--config",
                    str(cfg),
                    "run",
                    "--invocation",
                    str(inv),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            start = json.loads(launched.stdout)
            self.assertEqual(start["status"], "STARTED")
            self.assertIn(str(cfg), start["status_command"])
            job_id = start["job_id"]

            final = None
            for _ in range(100):
                checked = subprocess.run(
                    [
                        sys.executable,
                        str(CLI_PATH),
                        "--config",
                        str(cfg),
                        "status",
                        "--job",
                        job_id,
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                final = json.loads(checked.stdout)
                if final["status"] != "RUNNING":
                    break
                time.sleep(0.05)

            self.assertIsNotNone(final)
            self.assertEqual(final["status"], "FAILED")
            self.assertEqual(final["failure_code"], "FAILED_PREFLIGHT")


if __name__ == "__main__":
    unittest.main()
