import unittest
from support import finding, result
from loop_review.ledger import discovery_claims, resolve_claims, final_status


class LedgerTests(unittest.TestCase):
    def test_discovery_origin_plus_independent_accept_confirms_claim(self):
        claims = discovery_claims({'reviewer-a': result(None, [finding()])})
        decision = {'finding_id': 'F001', 'decision': 'ACCEPT', 'rationale': 'Verified.', 'evidence': finding()['evidence']}
        resolved = resolve_claims(claims, {'reviewer-b': result(None, adjudications=[decision])})
        self.assertEqual(resolved[0]['status'], 'ACCEPTED')
        self.assertEqual(final_status(resolved, [], 'full'), 'FROZEN_CHANGES_REQUIRED')

    def test_historical_fix_needs_two_independent_evidence_positions(self):
        claims = [{'id': 'H001', 'origin': 'history', 'finding': finding(), 'previous_id': 'F001'}]
        decision = {'finding_id': 'H001', 'decision': 'REJECT', 'rationale': 'Fixed.', 'evidence': finding()['evidence']}
        one = {'reviewer-a': result(None, adjudications=[decision])}
        self.assertEqual(resolve_claims(claims, one)[0]['status'], 'UNVERIFIED')
        one['reviewer-b'] = result(None, adjudications=[decision])
        self.assertEqual(resolve_claims(claims, one)[0]['status'], 'RESOLVED')
        self.assertEqual(final_status(resolve_claims(claims, one), [], 'fixes'), 'FIXES_VERIFIED')
