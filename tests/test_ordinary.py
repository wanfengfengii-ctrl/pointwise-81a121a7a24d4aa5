from dataclasses import replace
from fractions import Fraction

from capture.depot import RetirementState
from capture.evidence import EvidenceMismatch
from capture.intake import CaptureCommitFailed
from delivery.continuity import DeliverySegment
from recorder.pages import SlotRef
from recorder.status import DeviceUnavailable, RetirementOutcome
from review.discrepancies import ReviewExpectation
from review.workbench import initialize_workspace
from tests.fixtures import WorkspaceCase, make_page, summaries


class OrdinaryTests(WorkspaceCase):
    def test_conversion(self):
        desk = self.desk
        line = desk.lines.register('L', 6, 4)
        self.assertEqual((line.profile.numerator, line.profile.denominator), (3, 2))
        page = make_page('p', 'L', ('a', 1, 'V7', b'7'), ('b', 2, 'V11', b'11'))
        header = desk.feed.feed(page)
        self.assertEqual(desk.feed.read_page('p'), page)
        accepted = desk.intake.accept('p')
        self.assertEqual(tuple(slot.evidence() for slot in accepted), header.slots)
        result = desk.publisher.publish('L', DeliverySegment(2, ('p',)))
        expected = [('V7', 1, 'p', 'a', 21, 2), ('V11', 2, 'p', 'b', 33, 2)]
        self.assertEqual(summaries(result.readings), expected)
        self.assertEqual(summaries(desk.lookup.provenance(result.result_id)), expected)
        self.assertEqual(result.receipt.unit, 'um/s')
        self.assertEqual((result.receipt.start, result.receipt.through), (1, 2))
        self.assertEqual(desk.results.get_frontier('L'), 2)

    def test_invalid_profile(self):
        for terms in ((0, 1), (1, 0), (1, -2), (-1, 2), (True, 2), (1, 1.5)):
            with self.subTest(terms=terms), self.assertRaises(ValueError):
                self.desk.lines.register('L', *terms)
        with self.assertRaises(KeyError):
            self.desk.lines.get_line('L')
        self.desk.lines.register('L', 7, 5)
        with self.assertRaises(ValueError):
            self.desk.lines.register('L', 1, 1)
        self.assertEqual(self.desk.lines.get_line('L').profile.multiplier, Fraction(7, 5))

    def test_empty_page(self):
        desk = self.desk
        desk.lines.register('L', 1, 1)
        page = make_page('empty', 'L')
        desk.feed.feed(page)
        self.assertEqual(desk.feed.read_page('empty'), page)
        self.assertEqual(desk.intake.accept('empty'), ())
        self.assertEqual(desk.depot.get_header('empty').slots, ())
        self.assertIsNone(desk.publisher.publish('L', DeliverySegment(1, ('empty',))))
        self.assertIsNone(desk.results.get_frontier('L'))
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_results').fetchone()[0], 0)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0], 0)

    def test_signed_zero(self):
        desk = self.desk
        desk.lines.register('L', 3, 2)
        desk.feed.feed(make_page('p', 'L', ('a', 1, 'zero', b'+0'), ('b', 2, 'negative', b'-7')))
        desk.intake.accept('p')
        result = desk.publisher.publish('L', DeliverySegment(2, ('p',)))
        self.assertEqual(summaries(desk.lookup.review(result.result_id).readings),
                         [('zero', 1, 'p', 'a', 0, 1), ('negative', 2, 'p', 'b', -21, 2)])

    def test_same_slot_different_pages(self):
        desk = self.desk
        desk.lines.register('L', 3, 2)
        desk.feed.feed(make_page('p1', 'L', ('a', 1, 'V7', b'7')))
        desk.feed.feed(make_page('p2', 'L', ('a', 2, 'V11', b'11')))
        first = desk.intake.accept('p1')
        self.assertEqual(desk.intake.accept('p1'), first)
        desk.intake.accept('p2')
        self.assertEqual(desk.depot.get_slot(SlotRef('p2', 'a')).payload, b'11')
        self.assertEqual(desk.depot.get_slot(SlotRef('p1', 'a')).payload, b'7')
        result = desk.publisher.publish('L', DeliverySegment(2, ('p2', 'p1')))
        self.assertEqual(summaries(result.readings), [('V7', 1, 'p1', 'a', 21, 2),
                                                     ('V11', 2, 'p2', 'a', 33, 2)])
        self.assertEqual(desk.publisher.publish('L', DeliverySegment(2, ('p1', 'p2'))), result)

    def test_accept_replay(self):
        desk = self.desk
        desk.lines.register('L', 1, 1)
        desk.feed.feed(make_page('p', 'L', ('a', 1, 'V7', b'007')))
        first = desk.intake.accept('p', ('a',))
        self.assertEqual(first[0].payload, b'007')
        self.assertEqual(desk.intake.accept('p'), first)
        self.assertIs(desk.retirement.retire('p'), RetirementState.CONFIRMED)
        desk = self.reopen()
        desk.device.stop()
        self.assertEqual(desk.intake.accept('p'), first)
        with self.assertRaises(DeviceUnavailable):
            desk.feed.read_slot(SlotRef('p', 'a'))
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM custody_slots').fetchone()[0], 1)

    def test_initialize_existing(self):
        desk = self.desk
        desk.lines.register('crew-line', 5, 4)
        desk.feed.feed(make_page('crew-page', 'crew-line', ('c', 1, 'crew', b'12')))
        desk.intake.accept('crew-page')
        request = DeliverySegment(1, ('crew-page',))
        result = desk.publisher.publish('crew-line', request)
        desk.shift.begin_batch('crew-shift')
        desk.shift.register_item('crew-shift', 'crew-item', result.result_id,
                                 (ReviewExpectation(1, 'crew', SlotRef('crew-page', 'c'), Fraction(15)),))
        reviewed = desk.shift.record_review('crew-shift', 'crew-item')
        initialize_workspace(self.path)
        initialize_workspace(self.path)
        desk = self.reopen()
        self.assertEqual(desk.lines.get_line('crew-line').profile.multiplier, Fraction(5, 4))
        self.assertEqual(desk.feed.read_slot(SlotRef('crew-page', 'c')).payload, b'12')
        self.assertEqual(desk.depot.get_slot(SlotRef('crew-page', 'c')).payload, b'12')
        self.assertEqual(desk.lookup.review(result.result_id), result)
        self.assertEqual(desk.publisher.publish('crew-line', request), result)
        self.assertEqual(desk.shift.get_batch('crew-shift').items, (reviewed,))
        self.assertEqual(reviewed.discrepancies, ())

    def test_bad_slot(self):
        desk = self.desk
        desk.lines.register('L', 1, 1)
        for position, raw in enumerate((b'', b'7.0', b' 7'), 1):
            with self.subTest(raw=raw):
                page_id = f'bad-{position}'
                page = make_page(page_id, 'L', ('a', position, 'bad', raw))
                desk.feed.feed(page)
                with self.assertRaises(EvidenceMismatch):
                    desk.intake.accept(page_id)
                self.assertIsNone(desk.depot.get_header(page_id))
                self.assertIsNone(desk.depot.get_slot(SlotRef(page_id, 'a')))
                self.assertEqual(desk.feed.read_page(page_id), page)
        page = make_page('corrupt', 'L', ('c', 4, 'valid', b'11'))
        desk.feed.feed(page)
        desk.feed.slot_replies[page.slots[0].ref] = replace(page.slots[0], payload=b'12')
        with self.assertRaisesRegex(EvidenceMismatch, 'bytes'):
            desk.intake.accept('corrupt')
        self.assertIsNone(desk.depot.get_header('corrupt'))
        self.assertIs(desk.status.query_retirement('corrupt'), RetirementOutcome.RETAINED)

    def test_slot_binding(self):
        desk = self.desk
        desk.lines.register('L', 1, 1)
        page = make_page('p', 'L', ('a', 1, 'V7', b'7'), ('b', 2, 'V11', b'11'))
        desk.feed.feed(page)
        first, second = page.slots
        desk.intake.accept('p', ('b',))
        desk.feed.slot_replies[first.ref] = replace(first, ref=second.ref)
        with self.assertRaisesRegex(EvidenceMismatch, 'identity'):
            desk.intake.accept('p', ('a',))
        self.assertIsNone(desk.depot.get_slot(first.ref))
        self.assertEqual(desk.depot.get_slot(second.ref).payload, b'11')
        desk.feed.slot_replies.clear()
        self.assertEqual(desk.feed.read_page('p'), page)

    def test_commit_failure(self):
        desk = self.desk
        desk.lines.register('L', 1, 1)
        page = make_page('p', 'L', ('a', 1, 'V7', b'7'), ('b', 2, 'V11', b'11'),
                         ('c', 3, 'V13', b'13'))
        desk.feed.feed(page)
        first = desk.intake.accept('p', ('a',))[0]
        desk.intake.fail_next.add('p')
        with self.assertRaises(CaptureCommitFailed):
            desk.intake.accept('p', ('b', 'c'))
        desk = self.reopen()
        self.assertEqual(desk.depot.slots_for_page('p'), (first,))
        self.assertIs(desk.retirement.retire('p'), RetirementState.INCOMPLETE)
        self.assertEqual(desk.feed.read_page('p'), page)
        self.assertEqual([slot.payload for slot in desk.intake.accept('p')], [b'7', b'11', b'13'])
