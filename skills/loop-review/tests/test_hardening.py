import json
import shutil
import subprocess
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

    def test_changed_prompt_invalidates_previous_call_checkpoint(self):
        skill = self.base / 'skill'
        shutil.copytree(self.controller.skill_root / 'references', skill / 'references')
        self.controller = LoopReviewController(self.cfg, skill)
        self.controller.reviewers = {'reviewer-a': self.a, 'reviewer-b': self.b}
        self.b.fail = True
        directory = self.failed_run()
        rubric = skill / 'references' / 'design-rubric.md'
        rubric.write_text(rubric.read_text() + '\nNew evidence requirement.')
        self.b.fail = False
        self.controller.resume(directory)
        self.assertEqual(len(self.a.prompts), 2)

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
