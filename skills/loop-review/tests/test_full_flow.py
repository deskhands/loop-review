import json
from pathlib import Path

from support import ReviewCase, FakeReviewer, finding
from loop_review.util import LoopReviewError


class FullFlowTests(ReviewCase):
    def test_empty_complete_discovery_finishes_after_two_tasks(self):
        state = self.controller.run(self.invocation())
        self.assertEqual(state['status'], 'FROZEN_PASS')
        self.assertEqual(state['review_calls'], 2)
        self.assertEqual(len(self.a.prompts), 1)
        self.assertEqual(len(self.b.prompts), 1)
        directory = Path(state['run_dir'])
        self.assertIn('运行时迁移', str(directory))
        self.assertIn('初版方案审核', directory.name)
        self.assertEqual(state['run_id'], state['job_id'])
        self.assertIn('final-review.md', (directory.parent / 'README.md').read_text())
        self.assertTrue((directory / 'report.md').is_file())

    def test_two_discoveries_are_cross_verified_in_four_tasks(self):
        self.a.defect = self.b.defect = True
        state = self.controller.run(self.invocation())
        self.assertEqual(state['status'], 'FROZEN_CHANGES_REQUIRED')
        self.assertEqual(state['review_calls'], 4)
        output = json.loads((Path(state['run_dir']) / 'result.json').read_text())
        self.assertEqual([item['status'] for item in output['findings']], ['ACCEPTED', 'ACCEPTED'])
        self.assertNotIn('Leaked resource', self.a.prompts[0])
        self.assertNotIn('Leaked resource', self.b.prompts[0])

    def test_verification_new_issue_never_starts_another_cycle(self):
        self.a.defect = self.b.defect = self.a.new = True
        state = self.controller.run(self.invocation())
        self.assertEqual(state['status'], 'UNRESOLVED_MAX_CYCLES')
        self.assertEqual(state['review_calls'], 4)
        output = json.loads((Path(state['run_dir']) / 'result.json').read_text())
        self.assertEqual(output['findings'][-1]['status'], 'UNVERIFIED')

    def test_disagreement_is_a_terminal_result(self):
        self.a.defect = self.b.defect = True
        self.a.vote = 'REJECT'
        state = self.controller.run(self.invocation())
        self.assertEqual(state['status'], 'FROZEN_DISPUTED')
        self.assertEqual(state['review_calls'], 4)

    def test_open_question_prevents_empty_pass(self):
        self.a.questions = ['Required caller context is missing.']
        self.assertEqual(self.controller.run(self.invocation())['status'], 'UNRESOLVED_MAX_CYCLES')

    def test_failure_preserves_peer_finding_and_resume_reuses_it(self):
        self.a.defect = True
        self.b.fail = True
        directory = self.failed_run()
        report = (directory / 'report.md').read_text()
        self.assertIn('Leaked resource', report)
        self.assertIn('partial evidence', report)
        self.b.fail = False
        state = self.controller.resume(directory)
        self.assertEqual(state['status'], 'FROZEN_CHANGES_REQUIRED')
        self.assertEqual(len(self.a.prompts), 1)
        self.assertEqual(len(self.b.prompts), 3)  # Failed discovery, recovery, verification.
        self.assertEqual(state['review_calls'], 4)

    def test_resume_preserves_total_attempt_limit(self):
        self.b.fail = True
        directory = self.failed_run()
        for _ in range(4):
            with self.assertRaises(LoopReviewError):
                self.controller.resume(directory)
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'RUN_BUDGET_EXCEEDED')
        state = json.loads((directory / 'status.json').read_text())
        self.assertEqual(state['review_calls'], 6)
        self.assertEqual(len(self.a.prompts), 1)

    def test_token_budget_is_kept_across_resume(self):
        self.controller.config['limits']['max_total_tokens'] = 100
        self.a.tokens, self.b.tokens = 60, 60
        directory = self.failed_run()
        calls = len(self.a.prompts) + len(self.b.prompts)
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'RUN_BUDGET_EXCEEDED')
        self.assertEqual(len(self.a.prompts) + len(self.b.prompts), calls)

    def test_input_drift_and_model_change_fail_closed(self):
        self.b.fail = True
        directory = self.failed_run()
        self.controller.config['reviewers']['qwen']['model'] = 'another-model'
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'RESUME_CONFIG_CHANGED')
        self.controller.config['reviewers']['qwen']['model'] = 'qwen/qwen3.8-flash'
        (self.repo / 'design.md').write_text('Changed design.\n')
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'FAILED_INPUT_CHANGED')

    def test_history_is_withheld_until_verification(self):
        self.a.defect = True
        previous = self.controller.run(self.invocation())
        self.a.defect = False
        (self.repo / 'design.md').write_text('Cleanup now guaranteed.\n')
        self.a.vote = self.b.vote = 'REJECT'
        self.a.prompts.clear()
        self.b.prompts.clear()
        state = self.controller.run(self.invocation(previous_run_id=previous['run_id'], title='修改方案复核'))
        self.assertEqual(state['status'], 'FROZEN_PASS')
        self.assertEqual(state['review_calls'], 4)
        self.assertNotIn('Leaked resource', self.a.prompts[0])
        self.assertIn('Leaked resource', self.a.prompts[1])
        self.assertEqual(Path(previous['run_dir']).parent, Path(state['run_dir']).parent)
        output = json.loads((Path(state['run_dir']) / 'result.json').read_text())
        self.assertEqual(output['findings'][0]['status'], 'RESOLVED')

    def test_fixes_scope_only_checks_specified_claims(self):
        self.a.defect = True
        previous = self.controller.run(self.invocation())
        self.a.vote = self.b.vote = 'REJECT'
        state = self.controller.run(self.invocation(previous_run_id=previous['run_id'], scope='fixes'))
        self.assertEqual(state['status'], 'FIXES_VERIFIED')
        self.assertEqual(state['review_calls'], 2)
        self.assertIn('not a full review', (Path(state['run_dir']) / 'report.md').read_text())

    def test_shared_task_directory_does_not_implicitly_load_history(self):
        self.a.defect = True
        self.controller.run(self.invocation())
        self.a.defect = False
        self.a.prompts.clear()
        state = self.controller.run(self.invocation(title='独立复核'))
        self.assertEqual(state['review_calls'], 2)
        self.assertNotIn('Leaked resource', self.a.prompts[0])
