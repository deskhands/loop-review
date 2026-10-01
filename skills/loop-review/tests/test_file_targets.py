import json
from pathlib import Path

from support import ReviewCase
from loop_review.util import LoopReviewError


class FileTargetTests(ReviewCase):
    def target(self):
        nested = self.repo / 'docs'
        nested.mkdir(exist_ok=True)
        (nested / 'AGENTS.md').write_text('Read the whole design.\n')
        second = nested / 'second.md'
        second.write_text('Second required design.\n')
        return {'kind': 'files', 'paths': [str(self.repo / 'design.md'), str(second)]}

    def test_all_files_and_nested_policies_are_explicit_in_prompts(self):
        target = self.target()
        state = self.controller.run(self.invocation(target=target), dry_run=True)
        directory = Path(state['run_dir'])
        prompt = (directory / 'audit/discovery/reviewer-a/prompt.md').read_text()
        for path in target['paths']:
            self.assertIn(path, prompt)
        self.assertIn(str(self.repo / 'docs/AGENTS.md'), prompt)
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertEqual(len(manifest['input_fingerprint']['target_files']), 2)

    def test_changing_any_file_prevents_checkpoint_reuse(self):
        target = self.target()
        self.a.policy = self.b.policy = None  # Fail execution; no valid policy acknowledgments.
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.run(self.invocation(target=target))
        directory = Path(caught.exception.details['run_dir'])
        Path(target['paths'][1]).write_text('Changed.\n')
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'FAILED_INPUT_CHANGED')

    def test_empty_and_duplicate_files_are_rejected_before_calls(self):
        for paths in ([], [str(self.repo / 'design.md')] * 2):
            with self.subTest(paths=paths), self.assertRaises(LoopReviewError) as caught:
                self.controller.run(self.invocation(target={'kind': 'files', 'paths': paths}), dry_run=True)
            self.assertEqual(caught.exception.code, 'INVALID_INVOCATION')
