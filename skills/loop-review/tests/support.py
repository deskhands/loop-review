import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from loop_review.config import load_config
from loop_review.controller import LoopReviewController
from loop_review.util import LoopReviewError


def result(policy, findings=None, adjudications=None, questions=None):
    return {'schema_version': '2.0', 'summary': 'Review complete.',
            'policies_checked': [str(policy)] if policy else [], 'findings': findings or [],
            'adjudications': adjudications or [], 'open_questions': questions or []}


def finding(title='Leaked resource'):
    return {'severity': 'HIGH', 'blocking': True, 'category': 'correctness', 'title': title,
            'claim': 'The error path leaks the acquired resource.',
            'evidence': [{'path': 'design.md', 'line_start': 1, 'line_end': 1, 'description': 'Error path lacks cleanup.'}],
            'rule_refs': [], 'rationale': 'Repeated failures exhaust resources.', 'required_change': 'Release on failure.'}


class FakeReviewer:
    def __init__(self, policy, *, defect=False, fail=False, vote='ACCEPT', new=False, tokens=None, questions=None):
        self.policy, self.defect, self.fail = policy, defect, fail
        self.vote, self.new, self.tokens, self.questions = vote, new, tokens, questions
        self.prompts = []

    def parse_saved_output(self, raw):
        return json.loads(raw)

    def healthcheck(self, cwd):
        return {'ok': True}

    def review(self, prompt, cwd, run_dir, out_dir, read_dirs=None):
        self.prompts.append(prompt)
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.tokens is not None:
            self.progress({'total_tokens': self.tokens, 'turns': 1, 'tool_calls': 1})
        if self.fail:
            raise LoopReviewError('PROVIDER_REQUEST_FAILED', 'Synthetic provider failure')
        claims = json.loads(prompt.split('[CLAIMS TO VERIFY]\n')[1].split('\n\n[OUTPUT]')[0])
        verification = 'Verify ONLY' in prompt
        adj = [{'finding_id': item['id'], 'decision': self.vote, 'rationale': 'Checked against current source.',
                'evidence': item['finding']['evidence']} for item in claims]
        findings = [finding()] if (self.defect and not verification) or (self.new and verification) else []
        output = result(self.policy, findings, adj, self.questions)
        (out_dir / 'raw.stdout').write_text(json.dumps(output))
        (out_dir / 'raw.stderr').write_text('')
        (out_dir / 'stream.json').write_text(json.dumps({'exit_code': 0, 'turns': 1, 'tool_calls': 1,
                                                       'total_tokens': self.tokens, 'reported_cost': None}))
        return {'result': output, 'meta': {'status': 'OK', 'exit_code': 0}}


class ReviewCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.repo = self.base / 'DeskHands-next'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.policy = self.repo / 'AGENTS.md'
        self.policy.write_text('Keep the implementation simple.\n')
        (self.repo / 'design.md').write_text('Release resources on failure.\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.run_root = self.base / 'reviews'
        self.config_path = self.base / 'config.toml'
        self.config_path.write_text(f'''version = 2
[paths]
run_root = "{self.run_root}"
[reviewers.qwen]
adapter = "pi"
executable = "/bin/false"
model = "qwen/qwen3.8-flash"
reasoning = "high"
timeout_seconds = 10
[reviewers.deepseek]
adapter = "claude"
executable = "/bin/false"
model = "deepseek-flash[1m]"
reasoning = "max"
timeout_seconds = 10
''')
        self.cfg = load_config(self.config_path)
        self.controller = LoopReviewController(self.cfg, SKILL)
        self.a, self.b = FakeReviewer(self.policy), FakeReviewer(self.policy)
        self.controller.reviewers = {'reviewer-a': self.a, 'reviewer-b': self.b}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def invocation(self, **overrides):
        value = {'schema_version': '2.0', 'mode': 'design', 'repo': str(self.repo),
                 'title': '初版方案审核', 'task': {'id': 'issue-91', 'title': '运行时迁移'},
                 'request': {'kind': 'text', 'content': 'Review this exact design.'},
                 'target': {'kind': 'file', 'path': str(self.repo / 'design.md')}}
        value.update(overrides)
        path = self.base / 'invocation.json'
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path

    def failed_run(self):
        try:
            self.controller.run(self.invocation())
        except LoopReviewError as error:
            return Path(error.details['run_dir'])
        self.fail('Expected failure')
