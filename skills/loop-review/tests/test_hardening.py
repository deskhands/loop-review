import json
import shutil
import subprocess
import threading
from pathlib import Path

from support import ReviewCase, FakeReviewer
from loop_review.controller import LoopReviewController
from loop_review.util import LoopReviewError


class HardeningTests(ReviewCase):
    def test_worktrees_share_repository_and_task_but_clones_do_not(self):
        first = self.controller.run(self.invocation(), dry_run=True)
        worktree = self.base / 'worktree'
        self.git('worktree', 'add', '-qb', 'feature', str(worktree))
        second = self.controller.run(self.invocation(repo=str(worktree), target={'kind': 'file', 'path': str(worktree / 'design.md')}), dry_run=True)
        self.assertEqual(first['repo_id'], second['repo_id'])
        self.assertEqual(Path(first['run_dir']).parent, Path(second['run_dir']).parent)
        clone = self.base / 'clones' / 'DeskHands-next'
        clone.parent.mkdir()
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(clone)], check=True)
        third = self.controller.run(self.invocation(repo=str(clone), target={'kind': 'file', 'path': str(clone / 'design.md')}), dry_run=True)
        self.assertNotEqual(first['repo_id'], third['repo_id'])
        self.assertNotEqual(Path(first['run_dir']).parents[1], Path(third['run_dir']).parents[1])

    def test_same_title_without_explicit_task_creates_separate_tasks(self):
        first = self.controller.run(self.invocation(task={}), dry_run=True)
        second = self.controller.run(self.invocation(task={}), dry_run=True)
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertNotEqual(Path(first['run_dir']).parent, Path(second['run_dir']).parent)

    def test_completed_run_is_not_overwritten_by_resume(self):
        state = self.controller.run(self.invocation())
        directory = Path(state['run_dir'])
        original = (directory / 'status.json').read_bytes()
        with self.assertRaises(LoopReviewError):
            self.controller.resume(directory)
        self.assertEqual((directory / 'status.json').read_bytes(), original)

    def test_request_tampering_cannot_reuse_checkpoints(self):
        self.a.defect = True
        self.b.fail = True
        directory = self.failed_run()
        (directory / 'input' / 'request.md').write_text('Changed request')
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'RESUME_CHECKPOINT_INVALID')
        self.assertIn('Leaked resource', (directory / 'report.md').read_text())

    def test_raw_success_can_be_recovered_without_another_model_attempt(self):
        self.a.defect = True
        self.b.fail = True
        directory = self.failed_run()
        checkpoint = directory / 'audit' / 'discovery' / 'reviewer-a' / 'checkpoint.json'
        saved = json.loads(checkpoint.read_text())
        (checkpoint.parent / saved['attempt'] / 'result.json').unlink()
        checkpoint.unlink()
        self.b.fail = False
        self.controller.resume(directory)
        self.assertEqual(len(self.a.prompts), 1)

    def test_changed_prompt_requires_new_run_before_any_model_call(self):
        skill = self.base / 'skill'
        shutil.copytree(self.controller.skill_root / 'references', skill / 'references')
        self.controller = LoopReviewController(self.cfg, skill)
        self.controller.reviewers = {'reviewer-a': self.a, 'reviewer-b': self.b}
        self.b.fail = True
        directory = self.failed_run()
        rubric = skill / 'references' / 'design-rubric.md'
        rubric.write_text(rubric.read_text() + '\nNew evidence requirement.')
        self.b.fail = False
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'RESUME_CONFIG_CHANGED')
        self.assertEqual(len(self.a.prompts), 1)
        self.assertEqual(len(self.b.prompts), 1)

    def test_run_root_inside_repository_is_rejected(self):
        self.cfg['paths']['run_root'] = str(self.repo / 'reviews')
        controller = LoopReviewController(self.cfg, self.controller.skill_root)
        with self.assertRaises(LoopReviewError) as caught:
            controller.run(self.invocation(), dry_run=True)
        self.assertEqual(caught.exception.code, 'CONFIG_ERROR')

    def test_prompt_limit_fails_before_model_call(self):
        self.controller.config['limits']['max_prompt_bytes'] = 10
        directory = self.failed_run()
        self.assertEqual(len(self.a.prompts) + len(self.b.prompts), 0)
        self.assertEqual(json.loads((directory / 'status.json').read_text())['failure_code'], 'PROMPT_LIMIT_EXCEEDED')

    def test_resume_preserves_new_cancel_after_launcher_transition(self):
        self.b.fail = True
        directory = self.failed_run()
        status = json.loads((directory / 'status.json').read_text())
        status['status'] = 'RESUMING'
        (directory / 'status.json').write_text(json.dumps(status))
        (directory / 'audit' / 'cancel.json').write_text('{}')
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.resume(directory)
        self.assertEqual(caught.exception.code, 'CANCELED')
        self.assertEqual(len(self.a.prompts) + len(self.b.prompts), 2)

    def test_raw_recovery_reconciles_usage_before_budget_check(self):
        for checkpoint_exists in (True, False):
            with self.subTest(checkpoint_exists=checkpoint_exists):
                self.controller.config['limits']['max_total_tokens'] = 1000
                self.b.fail = True
                directory = self.failed_run()
                checkpoint = directory / 'audit' / 'discovery' / 'reviewer-a' / 'checkpoint.json'
                saved = json.loads(checkpoint.read_text())
                attempt = checkpoint.parent / saved['attempt']
                stream = json.loads((attempt / 'stream.json').read_text())
                stream['total_tokens'] = 1234
                (attempt / 'stream.json').write_text(json.dumps(stream))
                if not checkpoint_exists:
                    checkpoint.unlink()
                    (attempt / 'result.json').unlink()
                attempts = len(self.a.prompts) + len(self.b.prompts)
                with self.assertRaises(LoopReviewError) as caught:
                    self.controller.resume(directory)
                self.assertEqual(caught.exception.code, 'RUN_BUDGET_EXCEEDED')
                state = json.loads((directory / 'status.json').read_text())
                self.assertEqual(sum(p.get('total_tokens') or 0 for p in state['progress'].values()), 1234)
                self.assertEqual(len(self.a.prompts) + len(self.b.prompts), attempts)

    def test_completed_peer_findings_are_live_while_other_peer_runs(self):
        self.a.defect = True
        original = self.b.review
        started, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release reviewer')
            return original(*args, **kwargs)
        self.b.review = delayed
        prepared = self.controller.prepare(self.invocation())
        directory = Path(prepared['run_dir'])
        errors = []
        def execute():
            try:
                self.controller.execute(directory)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=execute)
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            import time
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                current = json.loads((directory / 'result.json').read_text())
                if current['findings']:
                    break
                time.sleep(.02)
            self.assertEqual(current['findings'][0]['finding']['title'], 'Leaked resource')
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(errors)

    def test_history_remains_in_partial_report_when_discovery_fails(self):
        self.a.defect = True
        previous = self.controller.run(self.invocation())
        self.a.fail = self.b.fail = True
        with self.assertRaises(LoopReviewError) as caught:
            self.controller.run(self.invocation(previous_run_id=previous['run_id']))
        directory = Path(caught.exception.details['run_dir'])
        findings = json.loads((directory / 'result.json').read_text())['findings']
        self.assertEqual(findings[0]['origin'], 'history')
        self.assertEqual(findings[0]['status'], 'UNVERIFIED')
