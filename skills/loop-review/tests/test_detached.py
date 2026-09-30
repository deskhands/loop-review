import json
import os
import subprocess
import sys
import time
from pathlib import Path

from support import ReviewCase, SKILL


class DetachedTests(ReviewCase):
    def cli(self, *args, check=True):
        return subprocess.run([sys.executable, str(SKILL / 'scripts' / 'loop_review.py'), '--config', str(self.config_path), *args],
                              capture_output=True, text=True, check=check)

    def fake_executables(self, delay=0):
        program = self.base / 'fake-reviewer'
        program.write_text(f'''#!{sys.executable}
import json, re, sys, time
prompt = sys.argv[-1]
policies = json.loads(prompt.split('[APPLICABLE AGENTS.md]\\n')[1].split('\\n\\n[CURRENT TARGET]')[0])
output = {{'schema_version': '2.0', 'summary': 'No issues.', 'policies_checked': policies,
          'findings': [], 'adjudications': [], 'open_questions': []}}
if '--provider' in sys.argv:
    print(json.dumps({{'type': 'turn_start'}}), flush=True)
    time.sleep({delay})
    print(json.dumps({{'type': 'message_end', 'message': {{'role': 'assistant', 'stopReason': 'stop',
          'content': [{{'type': 'text', 'text': json.dumps(output)}}],
          'usage': {{'totalTokens': 100, 'cost': {{'total': 0.01}}}}}}}}), flush=True)
else:
    print(json.dumps({{'type': 'assistant', 'message': {{'id': 'one', 'content': [], 'usage': {{'input_tokens': 30}}}}}}), flush=True)
    time.sleep({delay})
    print(json.dumps({{'type': 'result', 'is_error': False, 'structured_output': output,
          'num_turns': 1, 'usage': {{'input_tokens': 30, 'output_tokens': 10}}}}), flush=True)
''')
        program.chmod(0o755)
        self.config_path.write_text(self.config_path.read_text().replace('/bin/false', str(program)))

    def wait_terminal(self, run_id, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = json.loads(self.cli('status', '--run', run_id, '--json').stdout)
            if state['status'] not in ('INIT', 'PREPARED', 'RESUMING', 'DISCOVERY', 'VERIFICATION'):
                return state
            time.sleep(0.1)
        self.fail('Detached controller did not terminate')

    def test_detached_run_has_one_id_live_status_and_terminal_report(self):
        self.fake_executables(delay=0.4)
        state = json.loads(self.cli('run', '--invocation', str(self.invocation()), '--json').stdout)
        self.assertEqual(state['launch_status'], 'STARTED')
        self.assertEqual(state['run_id'], state['job_id'])
        final = self.wait_terminal(state['run_id'])
        self.assertEqual(final['status'], 'FROZEN_PASS')
        self.assertEqual(final['review_calls'], 2)
        self.assertEqual(sum(item['total_tokens'] for item in final['progress'].values()), 140)
        human = self.cli('status', '--run', state['run_id']).stdout
        self.assertIn('Completed: no confirmed blocking findings', human)
        listing = json.loads(self.cli('list', '--repo', str(self.repo), '--json').stdout)
        self.assertEqual(listing['runs'][0]['run_id'], state['run_id'])

    def test_cancel_stops_workers_and_can_resume_with_remaining_budget(self):
        self.fake_executables(delay=2)
        state = json.loads(self.cli('run', '--invocation', str(self.invocation()), '--json').stdout)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = json.loads(self.cli('status', '--run', state['run_id'], '--json').stdout)
            if current['status'] == 'DISCOVERY' and current['review_calls'] == 2:
                break
            time.sleep(0.05)
        self.cli('cancel', '--run', state['run_id'])
        canceled = self.wait_terminal(state['run_id'])
        self.assertEqual(canceled['status'], 'CANCELED')
        resumed = json.loads(self.cli('resume', '--run', state['run_id'], '--json').stdout)
        self.assertEqual(resumed['launch_status'], 'RESUME_STARTED')
        final = self.wait_terminal(state['run_id'])
        self.assertEqual(final['status'], 'FROZEN_PASS')
        self.assertEqual(final['review_calls'], 4)

    def test_orphan_detection_uses_run_state_not_old_controller_stdout(self):
        state = self.controller.prepare(self.invocation())
        directory = Path(state['run_dir'])
        (directory / 'audit').mkdir(exist_ok=True)
        (directory / 'audit' / 'job.json').write_text(json.dumps({'pid': 99999999}))
        (directory / 'audit' / 'controller.stdout').write_text(json.dumps({'status': 'FROZEN_PASS'}))
        current = json.loads(self.cli('status', '--run', state['run_id'], '--json').stdout)
        self.assertEqual(current['status'], 'ORPHANED')
        self.assertIn('Incomplete', (directory / 'report.md').read_text())

    def test_legacy_results_remain_inspectable(self):
        directory = self.run_root / '20260923__old-repo__design__abcdef'
        (directory / 'final').mkdir(parents=True)
        (directory / 'final' / 'status.json').write_text(json.dumps({'run_id': 'abcdef', 'status': 'FROZEN_PASS', 'run_dir': str(directory)}))
        output = json.loads(self.cli('status', '--run', 'abcdef', '--json').stdout)
        self.assertTrue(output['legacy'])
        self.assertNotEqual(self.cli('resume', '--run', 'abcdef', check=False).returncode, 0)
