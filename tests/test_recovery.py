from fractions import Fraction

from capture.depot import RetirementState
from delivery.continuity import ContinuityGap, DeliverySegment
from recorder.pages import SlotRef
from recorder.status import DeviceUnavailable, PageUnavailable, RetirementOutcome
from review.discrepancies import ReviewExpectation
from review.lookup import ResultUnavailable
from tests.fixtures import (EAST_EXPECTED, EAST_OLD, WEST_EXPECTED, WEST_OLD, WorkspaceCase,
                            accept_split, advance_east, make_split, summaries)


class RecoveryTests(WorkspaceCase):
    def _publish_initial(self):
        fixture = make_split(self.desk)
        accept_split(self.desk)
        east = self.desk.publisher.publish('east', fixture.east_segment)
        west = self.desk.publisher.publish('west', fixture.west_segment)
        return fixture, east, west

    def test_reclaim_reply_lost(self):
        desk = self.desk
        fixture = make_split(desk)
        accept_split(desk)
        desk.reclaimer.lose_reply.add('p-last')
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.PENDING)
        self.assertEqual(desk.depot.pending_retirements(), ('p-last',))
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETIRED)
        with self.assertRaises(PageUnavailable):
            desk.feed.read_slot(WEST_OLD)
        desk = self.reopen()
        desk.reclaimer.defer_next.add('p-last')
        self.assertEqual(desk.recovery.resume(), {'p-last': RetirementState.CONFIRMED})
        self.assertIn('p-last', desk.reclaimer.defer_next)
        self.assertEqual(desk.journal.retirement_count('p-last'), 1)
        self.assertEqual(desk.recovery.resume(), {})
        desk.device.stop()
        west = desk.publisher.publish('west', fixture.west_segment)
        self.assertEqual(summaries(desk.lookup.provenance(west.result_id)), WEST_EXPECTED)
        self.assertEqual(desk.intake.accept('p-last')[1].ref, WEST_OLD)
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.CONFIRMED)

    def test_retained_retry(self):
        desk = self.desk
        make_split(desk)
        accept_split(desk)
        desk.reclaimer.defer_next.add('p-last')
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.PENDING)
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETAINED)
        self.assertEqual(desk.journal.retirement_count('p-last'), 0)
        desk = self.reopen()
        self.assertEqual(desk.recovery.resume(), {'p-last': RetirementState.CONFIRMED})
        self.assertEqual(desk.journal.retirement_count('p-last'), 1)
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETIRED)
        self.assertEqual(desk.depot.get_slot(WEST_OLD).payload, b'11')

    def test_outcome_unavailable(self):
        desk = self.desk
        fixture = make_split(desk)
        accept_split(desk)
        desk.reclaimer.lose_reply.add('p-last')
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.PENDING)
        desk = self.reopen()
        desk.device.stop()
        with self.assertRaises(DeviceUnavailable):
            desk.status.query_retirement('p-last')
        self.assertEqual(desk.recovery.resume(), {'p-last': RetirementState.PENDING})
        self.assertEqual(desk.depot.pending_retirements(), ('p-last',))
        self.assertEqual(summaries(desk.publisher.publish('east', fixture.east_segment).readings), EAST_EXPECTED)
        self.assertEqual(summaries(desk.publisher.publish('west', fixture.west_segment).readings), WEST_EXPECTED)
        desk = self.reopen()
        self.assertEqual(desk.recovery.resume(), {'p-last': RetirementState.CONFIRMED})
        self.assertEqual(desk.journal.retirement_count('p-last'), 1)

    def test_fixed_receipt(self):
        fixture, east, west = self._publish_initial()
        desk = self.reopen()
        desk.device.stop()
        for line, segment, original, expected in (('east', fixture.east_segment, east, EAST_EXPECTED),
                                                   ('west', fixture.west_segment, west, WEST_EXPECTED)):
            with self.subTest(line=line):
                claimed = desk.lookup.review(original.result_id)
                self.assertEqual(claimed, original)
                self.assertEqual(summaries(claimed.readings), expected)
                self.assertEqual(desk.publisher.publish(line, segment), original)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_results').fetchone()[0], 2)
        self.assertNotEqual(east.receipt.receipt_id, west.receipt.receipt_id)

    def test_replay_after_progress(self):
        fixture, east, west = self._publish_initial()
        next_result = advance_east(self.desk)
        desk = self.reopen()
        self.assertEqual(desk.publisher.publish('east', fixture.east_segment), east)
        self.assertEqual(desk.publisher.publish('west', fixture.west_segment), west)
        self.assertEqual(desk.results.get_frontier('east'), 3)
        self.assertEqual(desk.results.get_frontier('west'), 1)
        self.assertNotEqual(east.receipt, next_result.receipt)
        with self.assertRaises(ContinuityGap):
            desk.publisher.publish('east', DeliverySegment(2))
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_results').fetchone()[0], 3)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0], 3)

    def test_history_after_progress(self):
        _, east, west = self._publish_initial()
        next_result = advance_east(self.desk)
        desk = self.reopen()
        desk.device.stop()
        for original, expected in ((east, EAST_EXPECTED), (west, WEST_EXPECTED),
                                   (next_result, [('E13', 3, 'e-next', 'S0', 13, 1)])):
            with self.subTest(result=original.result_id):
                self.assertEqual(desk.lookup.review(original.result_id).receipt, original.receipt)
                self.assertEqual(summaries(desk.lookup.provenance(original.result_id)), expected)

    def test_unknown_result(self):
        desk = self.desk
        desk.shift.begin_batch('shift')
        desk.shift.register_item('shift', 'unknown', 'result-unknown', ())
        with self.assertRaises(ResultUnavailable):
            desk.lookup.review('result-unknown')
        with self.assertRaises(ResultUnavailable):
            desk.shift.record_review('shift', 'unknown')
        with self.assertRaises(KeyError):
            desk.shift.record_review('shift', 'unregistered')
        item, = desk.shift.get_batch('shift').items
        self.assertIsNone(item.receipt_id)
        self.assertIsNone(item.discrepancies)
        self.assertEqual(item.actual, ())

    def test_shift_check(self):
        _, east, west = self._publish_initial()
        advance_east(self.desk)
        desk = self.reopen()
        desk.device.stop()
        desk.shift.begin_batch('night')
        east_check = (ReviewExpectation(1, 'E7', EAST_OLD, Fraction(7)),
                      ReviewExpectation(2, 'E9', SlotRef('e-first', 'S0'), Fraction(9)))
        west_check = (ReviewExpectation(1, 'W11', WEST_OLD, Fraction(11)),)
        desk.shift.register_item('night', 'east', east.result_id, east_check)
        desk.shift.register_item('night', 'west', west.result_id, west_check)
        wrong_check = (ReviewExpectation(2, 'E9', SlotRef('wrong', 'S0'), Fraction(19)),
                       east_check[0], ReviewExpectation(3, 'missing', SlotRef('e-next', 'S0'), Fraction(13)))
        desk.shift.register_item('night', 'difference', east.result_id, wrong_check)
        east_item = desk.shift.record_review('night', 'east')
        west_item = desk.shift.record_review('night', 'west')
        bad = desk.shift.record_review('night', 'difference')
        self.assertEqual(east_item.discrepancies, ())
        self.assertEqual(west_item.discrepancies, ())
        self.assertEqual(summaries(east_item.actual), EAST_EXPECTED)
        self.assertEqual(summaries(west_item.actual), WEST_EXPECTED)
        self.assertEqual([(d.position, d.field) for d in bad.discrepancies],
                         [(None, 'order'), (2, 'source'), (2, 'value'), (3, 'missing')])
        self.assertEqual(bad.receipt_id, east.receipt.receipt_id)
        desk = self.reopen()
        self.assertEqual(desk.shift.get_batch('night').items, (bad, east_item, west_item))
        self.assertEqual(desk.shift.record_review('night', 'difference'), bad)
