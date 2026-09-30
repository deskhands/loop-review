import unittest
from support import result, finding
from loop_review.schemas import validate_result
from loop_review.util import LoopReviewError


class SchemaTests(unittest.TestCase):
    def test_requires_exact_policy_acknowledgements(self):
        with self.assertRaises(LoopReviewError):
            validate_result(result(None), ['/repo/AGENTS.md'])

    def test_rejects_missing_and_invented_verification_ids(self):
        with self.assertRaises(LoopReviewError):
            validate_result(result(None), [], ['F001'])
        output = result(None, adjudications=[{'finding_id': 'F999', 'decision': 'ACCEPT', 'rationale': 'Observed.',
                                             'evidence': finding()['evidence']}])
        with self.assertRaises(LoopReviewError):
            validate_result(output, [], ['F001'])

    def test_confirmed_verification_requires_evidence(self):
        output = result(None, adjudications=[{'finding_id': 'F001', 'decision': 'ACCEPT', 'rationale': 'Observed.', 'evidence': []}])
        with self.assertRaises(LoopReviewError):
            validate_result(output, [], ['F001'])
        output['adjudications'][0]['decision'] = 'UNCERTAIN'
        self.assertEqual(validate_result(output, [], ['F001']), output)

    def test_rule_violation_is_derived_from_finding_without_secondary_ids(self):
        issue = finding()
        issue['rule_refs'] = [{'path': '/repo/AGENTS.md', 'description': 'Required cleanup.'}]
        output = result('/repo/AGENTS.md', [issue])
        self.assertEqual(validate_result(output, ['/repo/AGENTS.md']), output)
        issue['blocking'] = False
        with self.assertRaises(LoopReviewError):
            validate_result(output, ['/repo/AGENTS.md'])

    def test_rejects_extra_fields_and_invalid_evidence_ranges(self):
        output = result(None, [finding()])
        output['freeze_assessment'] = {}
        with self.assertRaises(LoopReviewError):
            validate_result(output, [])
        del output['freeze_assessment']
        output['findings'][0]['evidence'][0]['line_start'] = 0
        with self.assertRaises(LoopReviewError):
            validate_result(output, [])
