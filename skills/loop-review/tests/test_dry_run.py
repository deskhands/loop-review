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


class DryRunTests(unittest.TestCase):
    def test_design_dry_run_persists_original_text_and_rules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / "AGENTS.md").write_text("No overengineering.\n")
            (root / "README.md").write_text("x\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)

            cfg_path = Path(td) / "config.toml"
            cfg_path.write_text(
                'version = 1\n'
                '[paths]\nrun_root = "' + str(Path(td) / "runs") + '"\n'
                '[loop]\nmax_cycles = 2\nmax_model_calls = 6\n'
                '[reviewers.grok]\nadapter = "opencode"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "xhigh"\ntimeout_seconds = 1\n'
                '[reviewers.deepseek]\nadapter = "claude"\nexecutable = "/bin/false"\nmodel = "x"\nreasoning = "max"\ntimeout_seconds = 1\n'
            )
            cfg = load_config(cfg_path)
            controller = LoopReviewController(cfg, SKILL)
            inv = {
                "schema_version": "1.0",
                "mode": "design",
                "repo": str(root),
                "request": {"kind": "text", "content": "Review verbatim."},
                "target": {"kind": "text", "content": "# Design\nSimple."},
            }
            inv_path = Path(td) / "invocation.json"
            inv_path.write_text(json.dumps(inv))
            result = controller.run(inv_path, dry_run=True)
            self.assertEqual(result["status"], "PREPARED_DRY_RUN")
            run_dir = Path(result["run_dir"])
            self.assertEqual((run_dir / "input" / "target.md").read_text(), "# Design\nSimple.")
            prompt = (run_dir / "rounds" / "00-discovery" / "grok" / "prompt.md").read_text()
            self.assertIn(str((root / "AGENTS.md").resolve()), prompt)
            self.assertIn("Review verbatim.", prompt)
            self.assertIn("rule_refs is reserved exclusively", prompt)
            self.assertIn("ADRs, design documents, source files, tests, requirements", prompt)


if __name__ == "__main__":
    unittest.main()
