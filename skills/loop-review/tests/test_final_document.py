import json
from pathlib import Path

from support import ReviewCase, finding
from loop_review.renderer import publish_reports, render_final, report_context
from loop_review.util import atomic_json


class FinalDocumentTests(ReviewCase):
    def test_success_always_delivers_one_standalone_document(self):
        self.a.defect = True
        state = self.controller.run(self.invocation())
        directory = Path(state['run_dir'])
        document = directory / 'final-review.md'
        self.assertEqual(state['report_path'], str(document))
        self.assertTrue(document.is_file())
        text = document.read_text()
        self.assertIn('Review this exact design.', text)
        self.assertIn(str(self.repo / 'design.md'), text)
        self.assertIn('qwen/qwen3.8-flash', text)
        self.assertIn('Confirmed issues', text)
        self.assertIn('Checked against current source.', text)
        self.assertNotIn('## Execution', text)
        self.assertNotIn('(result.json)', text)
        self.assertEqual((directory / 'report.md').read_text(), text)
        self.assertTrue((directory / 'audit/execution.md').is_file())
        self.assertIn('final-review.md', (directory.parent / 'README.md').read_text())
        self.assertIn('Review complete.', text)

    def test_cross_evidence_and_actions_are_present_even_when_claim_is_rejected(self):
        prepared = self.controller.prepare(self.invocation(), dry_run=True)
        issue = finding()
        issue['rule_refs'] = [{'path': 'AGENTS.md', 'description': 'Reuse the existing cleanup primitive.'}]
        item = {'id': 'F001', 'origin': 'reviewer-a', 'finding': issue, 'status': 'DISPUTED',
                'positions': {'reviewer-b': {'decision': 'REJECT', 'rationale': 'The existing cleanup refutes the claim.',
                  'evidence': [{'path': 'cleanup.py', 'line_start': 12, 'line_end': 14, 'description': 'Finally releases the resource.'}]}}}
        state = {**prepared, 'status': 'UNRESOLVED_MAX_CYCLES'}
        text = render_final(state, {'findings': [item], 'open_questions': ['Unreviewed module.', 'Unreviewed module.']})
        self.assertIn('Contested / counter-evidence', text)
        self.assertIn('cleanup.py:12', text)
        self.assertIn('Finally releases the resource.', text)
        self.assertIn('Reuse the existing cleanup primitive.', text)
        self.assertIn('Do not apply the original requested change without checking the counter-evidence.', text)
        self.assertEqual(text.count('- Unreviewed module.'), 1)

    def test_failure_delivers_partial_document_without_an_empty_pass(self):
        self.b.fail = True
        directory = self.failed_run()
        text = (directory / 'final-review.md').read_text()
        self.assertIn('partial evidence', text)
        self.assertIn('PROVIDER_REQUEST_FAILED', text)
        self.assertNotIn('No confirmed blocking issue', text)

    def test_fixes_document_preserves_limited_scope(self):
        self.a.defect = True
        previous = self.controller.run(self.invocation())
        self.a.vote = self.b.vote = 'REJECT'
        state = self.controller.run(self.invocation(previous_run_id=previous['run_id'], scope='fixes'))
        text = Path(state['report_path']).read_text()
        self.assertIn('Resolved historical issues', text)
        self.assertIn('not a full review', text)

    def test_completed_review_with_scope_questions_still_has_final_advice(self):
        self.a.questions = ['Only one of the six requested documents was covered.']
        state = self.controller.run(self.invocation())
        text = Path(state['report_path']).read_text()
        self.assertEqual(state['status'], 'UNRESOLVED_MAX_CYCLES')
        self.assertIn('The review process has ended', text)
        self.assertIn(self.a.questions[0], text)
        self.assertNotIn('Final review recommendations are not yet available', text)

    def test_context_does_not_publish_tampered_checkpoint_summary(self):
        state = self.controller.run(self.invocation())
        directory = Path(state['run_dir'])
        slot = directory / 'audit/discovery/reviewer-a'
        saved = json.loads((slot / 'checkpoint.json').read_text())
        result_path = slot / saved['attempt'] / 'result.json'
        result_path.write_text(json.dumps({'summary': 'Tampered coverage claim.'}))
        publish_reports(directory, state, {'findings': [], 'open_questions': []})
        text = Path(state['report_path']).read_text()
        self.assertNotIn('Tampered coverage claim.', text)
        self.assertIn('checkpoint summary unavailable or invalid', text)

    def test_context_does_not_read_checkpoint_outside_its_slot(self):
        state = self.controller.prepare(self.invocation(), dry_run=True)
        directory = Path(state['run_dir'])
        slot = directory / 'audit/discovery/reviewer-a'
        atomic_json(slot / 'checkpoint.json', {'attempt': '../outside', 'result_sha256': 'irrelevant'})
        context = report_context(directory)
        self.assertEqual(context['summaries'], {})
        self.assertEqual(len(context['warnings']), 1)

    def test_final_document_names_every_target(self):
        second = self.repo / 'second.md'
        second.write_text('Second design document.')
        state = self.controller.run(self.invocation(target={'kind': 'files', 'paths': [str(self.repo / 'design.md'), str(second)]}))
        text = Path(state['report_path']).read_text()
        self.assertIn(str(second), text)
        self.assertIn(str(self.repo / 'design.md'), text)
