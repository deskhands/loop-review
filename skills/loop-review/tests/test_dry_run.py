import json
from pathlib import Path

from support import ReviewCase


class DryRunTests(ReviewCase):
    def test_original_text_and_readable_report_are_persisted_without_calls(self):
        invocation = self.invocation(target={'kind': 'text', 'content': '# Design\nSimple.'})
        state = self.controller.run(invocation, dry_run=True)
        directory = Path(state['run_dir'])
        self.assertEqual(state['status'], 'PREPARED_DRY_RUN')
        self.assertEqual((directory / 'input' / 'target.md').read_text(), '# Design\nSimple.')
        self.assertEqual(len(self.a.prompts) + len(self.b.prompts), 0)
        prompt = (directory / 'audit' / 'discovery' / 'reviewer-a' / 'prompt.md').read_text()
        self.assertIn(str(self.policy), prompt)
        self.assertIn('Review this exact design.', prompt)
        self.assertNotIn('freeze_assessment', prompt)
        self.assertNotIn('replacement_local_id', prompt)
