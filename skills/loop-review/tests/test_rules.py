import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from loop_review.agents_rules import discover_rules


class RulesTests(unittest.TestCase):
    def test_nested_agents_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text("root")
            (root / "src" / "api").mkdir(parents=True)
            (root / "src" / "AGENTS.md").write_text("src")
            target = root / "src" / "api" / "x.py"
            target.write_text("x=1")
            info = discover_rules(root, [target])
            paths = [Path(x["path"]).name for x in info["files"]]
            self.assertEqual(paths, ["AGENTS.md", "AGENTS.md"])
            mapping = info["map"][str(target.resolve())]
            self.assertEqual(len(mapping), 2)


if __name__ == "__main__":
    unittest.main()
