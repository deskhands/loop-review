import json
import threading
from pathlib import Path

from support import ReviewCase
from loop_review.execution import DEFAULT_LIMITS, EventMonitor
from loop_review.util import LoopReviewError


class BudgetTests(ReviewCase):
    def test_default_allocation_and_private_live_files(self):
        state = self.controller.run(self.invocation(), dry_run=True)
        directory = Path(state['run_dir'])
        for name in ('reviewer-a', 'reviewer-b'):
            discovery = json.loads((directory / 'audit' / 'discovery' / name / 'live-budget.json').read_text())
            verification = json.loads((directory / 'audit' / 'verification' / name / 'live-budget.json').read_text())
            self.assertEqual(discovery['token_limit'], 3_000_000)
            self.assertEqual(verification['token_limit'], 1_000_000)
            self.assertIsNone(discovery['known_tokens'])
            prompt = (directory / 'audit' / 'discovery' / name / 'prompt.md').read_text()
            self.assertIn(str(directory / 'audit' / 'discovery' / name / 'live-budget.json'), prompt)
            self.assertNotIn('reviewer-b' if name == 'reviewer-a' else 'reviewer-a', prompt)

    def test_fast_reviewer_exhaustion_does_not_cancel_peer(self):
        self.controller.config['limits']['max_total_tokens'] = 800
        exhausted = threading.Event()
        original_a, original_b = self.a.review, self.b.review
        self.a.defect, self.a.tokens, self.b.tokens = True, 100, 301
        def fast(*args, **kwargs):
            try:
                return original_b(*args, **kwargs)
            finally:
                exhausted.set()
        def slow(*args, **kwargs):
            self.assertTrue(exhausted.wait(3))
            self.a.cancel()  # Supervision of the slow peer must remain valid.
            return original_a(*args, **kwargs)
        self.a.review, self.b.review = slow, fast
        directory = self.failed_run()
        state = json.loads((directory / 'status.json').read_text())
        self.assertEqual(state['failure_code'], 'TASK_BUDGET_EXCEEDED')
        self.assertIn('Leaked resource', (directory / 'report.md').read_text())
        self.assertEqual(len(self.a.prompts), 1)
        self.assertEqual(len(self.b.prompts), 1)
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'TASK_BUDGET_EXCEEDED')
        self.assertEqual(len(self.a.prompts) + len(self.b.prompts), 2)

    def test_finish_warning_prevents_silent_empty_pass_and_survives_resume(self):
        self.controller.config['limits']['max_total_tokens'] = 800
        self.a.tokens, self.b.fail = 225, True
        directory = self.failed_run()
        budget = json.loads((directory / 'audit' / 'discovery' / 'reviewer-a' / 'live-budget.json').read_text())
        self.assertEqual(budget['action'], 'finish')
        self.assertEqual(budget['remaining_tokens'], 75)
        self.b.fail = False
        state = self.controller.resume(directory)
        self.assertEqual(state['status'], 'UNRESOLVED_MAX_CYCLES')
        output = json.loads((directory / 'result.json').read_text())
        self.assertTrue(any('coverage' in question for question in output['open_questions']))
        self.assertEqual(len(self.a.prompts), 1)

    def test_verification_has_reserved_tokens_and_fixes_get_full_share(self):
        self.controller.config['limits']['max_total_tokens'] = 800
        self.a.defect = self.b.defect = True
        original_a, original_b = self.a.review, self.b.review
        def consume(reviewer, original, *args, **kwargs):
            reviewer.tokens = 50 if 'Verify ONLY' in args[0] else 200
            return original(*args, **kwargs)
        self.a.review = lambda *a, **kw: consume(self.a, original_a, *a, **kw)
        self.b.review = lambda *a, **kw: consume(self.b, original_b, *a, **kw)
        previous = self.controller.run(self.invocation())
        self.assertEqual(previous['status'], 'FROZEN_CHANGES_REQUIRED')
        state = self.controller.run(self.invocation(previous_run_id=previous['run_id'], scope='fixes'), dry_run=True)
        budget = json.loads((Path(state['run_dir']) / 'audit' / 'verification' / 'reviewer-a' / 'live-budget.json').read_text())
        self.assertEqual(budget['token_limit'], 400)

    def test_raw_recovery_retains_warning_with_unknown_token_usage(self):
        original = self.a.review
        def tools_only(*args, **kwargs):
            self.a.progress({'turns': 24, 'tool_calls': 36, 'total_tokens': None})
            return original(*args, **kwargs)
        self.a.review, self.b.fail = tools_only, True
        directory = self.failed_run()
        slot = directory / 'audit' / 'discovery' / 'reviewer-a'
        checkpoint = json.loads((slot / 'checkpoint.json').read_text())
        (slot / 'checkpoint.json').unlink()
        (slot / checkpoint['attempt'] / 'result.json').unlink()
        self.b.fail = False
        state = self.controller.resume(directory)
        self.assertEqual(state['status'], 'UNRESOLVED_MAX_CYCLES')
        self.assertEqual(len(self.a.prompts), 1)
        budget = json.loads((slot / 'live-budget.json').read_text())
        self.assertTrue(budget['usage_unknown'])
        self.assertEqual(budget['action'], 'finish')

    def test_retry_consumes_same_phase_allowance(self):
        self.controller.config['limits']['max_total_tokens'] = 800
        self.b.tokens, self.b.fail = 160, True
        directory = self.failed_run()
        self.b.fail = False
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'TASK_BUDGET_EXCEEDED')
        budget = json.loads((directory / 'audit' / 'discovery' / 'reviewer-b' / 'live-budget.json').read_text())
        self.assertEqual(budget['known_tokens'], 320)
        self.assertEqual(len(self.a.prompts), 1)


class UsageTests(ReviewCase):
    def test_only_own_budget_reads_escape_identical_call_limit(self):
        monitor = EventMonitor(DEFAULT_LIMITS, budget_path=Path('/own/live-budget.json'))
        for _ in range(5):
            monitor.feed(json.dumps({'type': 'tool_execution_start', 'toolName': 'read',
                                    'args': {'path': '/own/live-budget.json'}}))
        self.assertEqual(monitor.stats['tool_calls'], 5)
        self.assertEqual(monitor.stats['max_identical_tool_calls'], 0)
        with self.assertRaises(LoopReviewError) as caught:
            for _ in range(4):
                monitor.feed(json.dumps({'type': 'tool_execution_start', 'toolName': 'Read',
                                        'args': {'file_path': '/peer/live-budget.json'}}))
        self.assertEqual(caught.exception.code, 'TASK_LIMIT_EXCEEDED')

    def test_claude_usage_deduplicates_blocks_and_includes_later_output(self):
        monitor = EventMonitor(DEFAULT_LIMITS)
        def feed(output):
            monitor.feed(json.dumps({'type': 'assistant', 'message': {'id': 'same', 'content': [],
                'usage': {'input_tokens': 10, 'output_tokens': output,
                          'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20}}}))
        feed(0)
        feed(5)
        feed(5)
        self.assertEqual(monitor.stats['turns'], 1)
        self.assertEqual(monitor.stats['total_tokens'], 135)
        self.assertEqual(monitor.stats['cache_read_tokens'], 100)
        self.assertEqual(monitor.stats['output_tokens'], 5)
        monitor.feed(json.dumps({'type': 'assistant', 'message': {'id': 'same', 'content': [],
                                                               'usage': {'output_tokens': 8}}}))
        self.assertEqual(monitor.stats['total_tokens'], 138)

    def test_pi_usage_breakdown_and_authoritative_claude_terminal(self):
        monitor = EventMonitor(DEFAULT_LIMITS)
        monitor.feed(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'usage': {
            'input': 10, 'output': 5, 'cacheRead': 100, 'cacheWrite': 20, 'totalTokens': 135}}}))
        self.assertEqual(monitor.stats['input_tokens'], 10)
        self.assertEqual(monitor.stats['cache_write_tokens'], 20)
        monitor.feed(json.dumps({'type': 'result', 'usage': {'input_tokens': 20, 'output_tokens': 10,
            'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 40}}))
        self.assertEqual(monitor.stats['total_tokens'], 270)
        self.assertEqual(monitor.stats['cache_read_tokens'], 200)
