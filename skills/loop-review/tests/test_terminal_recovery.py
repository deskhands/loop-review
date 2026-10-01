import json
from pathlib import Path

from support import ReviewCase, result, finding
from loop_review.adapters.claude import ClaudeAdapter
from loop_review.util import LoopReviewError


class TerminalRecoveryTests(ReviewCase):
    def rejected_terminal(self, **changes):
        self.b.fail = True
        directory = self.failed_run()
        attempt = next((directory / 'audit' / 'discovery' / 'reviewer-b').glob('attempt-*'))
        terminal = {'type': 'result', 'subtype': 'success', 'is_error': False,
                    'terminal_reason': 'completed', 'num_turns': 45,
                    'usage': {'input_tokens': 100, 'output_tokens': 20},
                    'structured_output': result(self.policy, findings=[finding()])}
        terminal.update(changes)
        events = [{'type': 'assistant', 'message': {'id': f'message-{index}', 'content': []}} for index in range(15)]
        events.append(terminal)
        (attempt / 'raw.stdout').write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        (attempt / 'raw.stderr').write_text('')
        (attempt / 'stream.json').write_text(json.dumps({'exit_code': None, 'status': 'FAILED', 'turns': 45,
                                                       'total_tokens': 120, 'duration_ms': 100}))
        (attempt / 'error.json').write_text(json.dumps({'code': 'TASK_LIMIT_EXCEEDED', 'message': 'Reviewer exceeded turns limit'}))
        # Exercise the actual Claude terminal parser while keeping paid calls fake.
        self.b.parse_saved_output = ClaudeAdapter('reviewer-b', {}).parse_saved_output
        return directory, attempt

    def test_false_terminal_limit_recovers_without_repeating_discovery(self):
        directory, attempt = self.rejected_terminal()
        originals = {name: (attempt / name).read_bytes() for name in ('raw.stdout', 'stream.json', 'error.json')}
        state = self.controller.resume(directory)
        self.assertEqual(state['status'], 'FROZEN_CHANGES_REQUIRED')
        self.assertEqual(state['review_calls'], 3)  # Two old discoveries, one peer verification.
        self.assertEqual(len(self.b.prompts), 1)
        key = str(attempt.relative_to(directory / 'audit'))
        self.assertEqual(state['progress'][key]['status'], 'RECOVERED')
        recovery = json.loads((attempt / 'recovery.json').read_text())
        self.assertEqual(recovery['stats']['turns'], 15)
        self.assertEqual(recovery['stats']['cli_reported_turns'], 45)
        for name, original in originals.items():
            self.assertEqual((attempt / name).read_bytes(), original)

    def test_error_terminal_and_true_turn_exhaustion_are_not_recovered(self):
        directory, attempt = self.rejected_terminal(subtype='error_max_turns', is_error=True)
        with self.assertRaises(LoopReviewError):
            self.controller.resume(directory)
        self.assertEqual(len(self.b.prompts), 2)
        self.assertFalse((attempt / 'recovery.json').exists())

    def test_missing_success_terminal_is_not_recovered(self):
        directory, attempt = self.rejected_terminal()
        lines = (attempt / 'raw.stdout').read_text().splitlines()
        (attempt / 'raw.stdout').write_text('\n'.join(lines[:-1]) + '\n')
        with self.assertRaises(LoopReviewError):
            self.controller.resume(directory)
        self.assertEqual(len(self.b.prompts), 2)
        self.assertFalse((attempt / 'recovery.json').exists())

    def test_true_observed_turn_exhaustion_is_not_recovered(self):
        directory, attempt = self.rejected_terminal()
        lines = (attempt / 'raw.stdout').read_text().splitlines()
        lines[-1:-1] = [json.dumps({'type': 'assistant', 'message': {'id': f'extra-{i}', 'content': []}}) for i in range(18)]
        (attempt / 'raw.stdout').write_text('\n'.join(lines) + '\n')
        with self.assertRaises(LoopReviewError):
            self.controller.resume(directory)
        self.assertEqual(len(self.b.prompts), 2)
        self.assertFalse((attempt / 'recovery.json').exists())

    def test_phase_token_limit_still_blocks_recovery(self):
        self.controller.config['limits']['max_total_tokens'] = 8000
        directory, attempt = self.rejected_terminal(usage={'input_tokens': 3001})
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'TASK_BUDGET_EXCEEDED')
        self.assertEqual(len(self.b.prompts), 1)
        self.assertFalse((attempt / 'recovery.json').exists())
