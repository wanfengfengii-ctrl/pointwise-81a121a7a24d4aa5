from fractions import Fraction

from capture.depot import RetirementState
from delivery.continuity import ContinuityGap
from delivery.publish import PublicationCommitFailed
from recorder.pages import SlotRef
from recorder.status import PageUnavailable, RetirementOutcome
from review.discrepancies import ReviewExpectation
from tests.fixtures import (EAST_EXPECTED, EAST_OLD, WEST_EXPECTED, WEST_OLD, WorkspaceCase,
                            make_split, summaries)


class JointTests(WorkspaceCase):
    def test_split_tail_handoff(self):
        desk = self.desk
        fixture = make_split(desk)
        desk.intake.accept('e-first')
        try:
            early = desk.publisher.publish('east', fixture.east_segment)
        except ContinuityGap:
            early = None
        desk.feed.paused_slots.add(WEST_OLD)
        desk.intake.accept('p-last', (EAST_OLD.coordinate,))
        requested = desk.retirement.retire('p-last')
        device_outcome = desk.status.query_retirement('p-last')
        if early is not None and device_outcome is RetirementOutcome.RETIRED:
            early_rows = summaries(desk.lookup.review(early.result_id).readings)
            with self.assertRaises(PageUnavailable):
                desk.feed.read_slot(WEST_OLD)
            if early_rows == [('E9', 2, 'e-first', 'S0', 9, 1)] and desk.depot.get_slot(WEST_OLD) is None:
                self.fail('SPLIT_TAIL_MISMATCH: east formally published E9 before E7 and retired p-last without W11 custody')
        self.assertIsNone(early)
        self.assertIs(requested, RetirementState.INCOMPLETE)
        self.assertIs(device_outcome, RetirementOutcome.RETAINED)
        self.assertIsNone(desk.depot.get_slot(WEST_OLD))
        desk.feed.paused_slots.remove(WEST_OLD)
        desk.intake.accept('p-last', (WEST_OLD.coordinate,))
        desk.publisher.fail_next.add('west')
        with self.assertRaises(PublicationCommitFailed):
            desk.publisher.publish('west', fixture.west_segment)
        self.assertIsNone(desk.results.get_frontier('west'))
        east = desk.publisher.publish('east', fixture.east_segment)
        self.assertEqual(summaries(desk.lookup.provenance(east.result_id)), EAST_EXPECTED)
        desk.reclaimer.lose_reply.add('p-last')
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.PENDING)
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETIRED)
        desk = self.reopen()
        self.assertEqual(desk.recovery.resume(), {'p-last': RetirementState.CONFIRMED})
        self.assertEqual(desk.journal.retirement_count('p-last'), 1)
        desk.device.stop()
        west = desk.publisher.publish('west', fixture.west_segment)
        self.assertEqual(summaries(desk.lookup.provenance(west.result_id)), WEST_EXPECTED)
        desk.shift.begin_batch('handoff')
        for line, segment, result, expected in (('east', fixture.east_segment, east, EAST_EXPECTED),
                                                ('west', fixture.west_segment, west, WEST_EXPECTED)):
            checklist = tuple(ReviewExpectation(position, label, SlotRef(page, coordinate), Fraction(n, d))
                              for label, position, page, coordinate, n, d in expected)
            desk.shift.register_item('handoff', line, result.result_id, checklist)
            reviewed = desk.shift.record_review('handoff', line)
            self.assertEqual(reviewed.receipt_id, result.receipt.receipt_id)
            self.assertEqual(reviewed.discrepancies, ())
            self.assertEqual(summaries(reviewed.actual), expected)
            self.assertEqual(desk.publisher.publish(line, segment), result)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0], 2)
