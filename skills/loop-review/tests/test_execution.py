import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import SKILL
from loop_review.adapters.pi import PiAdapter
from loop_review.execution import DEFAULT_LIMITS
from loop_review.util import LoopReviewError


class ExecutionTests(unittest.TestCase):
    def adapter(self, program, limits=None, progress=None, timeout=3):
        adapter = PiAdapter('reviewer-a', {'executable': str(program), 'model': 'unchanged-model',
                                         'reasoning': 'high', 'timeout_seconds': timeout})
        adapter.limits = {**DEFAULT_LIMITS, **(limits or {})}
        adapter.progress = progress
        return adapter

    def program(self, directory, body):
        program = directory / 'worker'
        program.write_text(f'#!{sys.executable}\n' + body)
        program.chmod(0o755)
        return program

    def test_raw_events_are_visible_while_worker_is_running(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            program = self.program(directory, "import json,time\nprint(json.dumps({'type':'turn_start'}),flush=True)\ntime.sleep(.4)\nprint(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'{\"ok\":true}'}]}}),flush=True)\n")
            observed = []
            def progress(stats):
                observed.append((stats['turns'], (directory / 'out' / 'raw.stdout').read_text()))
            output = self.adapter(program, progress=progress).review('prompt', directory, directory, directory / 'out')
            self.assertEqual(output['result'], {'ok': True})
            self.assertEqual(observed[0][0], 1)
            self.assertNotIn('message_end', observed[0][1])

    def test_tool_loop_and_provider_retries_are_stopped_before_final_output(self):
        for event, limits in [({'type': 'tool_execution_start', 'toolName': 'read', 'args': {'path':'same'}}, {'max_identical_tool_calls': 2}),
                              ({'type': 'auto_retry_start'}, {'max_provider_retries': 1})]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as td:
                directory = Path(td)
                body = f"import json,time\nfor _ in range(10):\n print({json.dumps(json.dumps(event))},flush=True)\n time.sleep(.03)\n"
                program = self.program(directory, body)
                with self.assertRaises(LoopReviewError) as caught:
                    self.adapter(program, limits=limits).review('prompt', directory, directory, directory / 'out')
                self.assertEqual(caught.exception.code, 'TASK_LIMIT_EXCEEDED')
                self.assertTrue((directory / 'out' / 'raw.stdout').exists())
                self.assertFalse((directory / 'out' / 'process.json').exists())

    def test_timeout_kills_launcher_descendant_and_preserves_partial_events(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            child_code = "import os,time;open('child.pid','w').write(str(os.getpid()));time.sleep(10);open('survived','w').write('bad')"
            body = f"import subprocess,sys,json,time\nsubprocess.Popen([sys.executable,'-c',{child_code!r}])\nprint(json.dumps({{'type':'turn_start'}}),flush=True)\ntime.sleep(20)\n"
            program = self.program(directory, body)
            started = time.monotonic()
            with self.assertRaises(LoopReviewError) as caught:
                self.adapter(program, timeout=1).review('prompt', directory, directory, directory / 'out')
            self.assertEqual(caught.exception.code, 'TIMEOUT')
            self.assertLess(time.monotonic() - started, 3)
            pid = int((directory / 'child.pid').read_text())
            state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith('Z'), state)
            self.assertIn('turn_start', (directory / 'out' / 'raw.stdout').read_text())

    def test_worker_that_closes_pipes_is_still_supervised(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            program = self.program(directory, 'import os,time\nos.close(1);os.close(2);time.sleep(10)\n')
            with self.assertRaises(LoopReviewError) as caught:
                self.adapter(program, timeout=1).review('prompt', directory, directory, directory / 'out')
            self.assertEqual(caught.exception.code, 'TIMEOUT')
