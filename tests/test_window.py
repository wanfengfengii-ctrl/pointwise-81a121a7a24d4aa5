import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from capture.depot import RetirementState
from capture.evidence import EvidenceMismatch
from capture.intake import CaptureCommitFailed
from delivery.continuity import ContinuityGap, DeliverySegment
from recorder.feed import WindowRead
from recorder.pages import MAX_POSITION, RawSlot, SlotRef
from recorder.status import DeviceUnavailable, PageUnavailable
from tests.fixtures import WorkspaceCase, make_page, summaries
from window_trace import run_trace


def windowed_page(page_id='w', line='W'):
    return make_page(page_id, line, ('A', 1, 'VA', b'7'), ('B', 2, 'VB', b'11'))


class ReadWindowTraceTests(TestCase):
    def test_sample_trace(self):
        sample = Path(__file__).resolve().parents[1] / 'samples' / 'read_windows.json'
        with sample.open(encoding='utf-8') as handle:
            run_trace(json.load(handle))


class WindowPageTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.desk.lines.register('W', 3, 2)

    def test_window_validation(self):
        desk = self.desk
        with self.assertRaises(ValueError):
            desk.feed.feed_windowed(make_page('empty', 'W'), {})
        page = windowed_page()
        bad_windows = [
            {'A': {1, 2}},
            {'A': {1, 2}, 'B': {1}, 'C': {1}},
            {'A': set(), 'B': {1}},
            {'A': {0}, 'B': {1}},
            {'A': {-2}, 'B': {1}},
            {'A': {True}, 'B': {1}},
            {'A': {'1'}, 'B': {1}},
            {'A': {MAX_POSITION + 1}, 'B': {1}},
            {'A': {1, 2}, 'B': '1'},
        ]
        for windows in bad_windows:
            with self.subTest(windows=windows), self.assertRaises(ValueError):
                desk.feed.feed_windowed(page, windows)
        with self.assertRaises(ValueError):
            desk.feed.feed_windowed(page, None)
        header = desk.feed.feed_windowed(page, {'A': {1, 2}, 'B': {1}})
        self.assertEqual(desk.status.page_header('w'), header)
        self.assertEqual(desk.journal.get_header('w'), header)
        with self.assertRaises(ValueError):
            desk.feed.feed_windowed(page, {'A': {1, 2}, 'B': {1}})

    def test_ordinary_access_blocked(self):
        desk = self.desk
        header = desk.feed.feed_windowed(windowed_page(), {'A': {1, 2}, 'B': {1}})
        with self.assertRaises(PageUnavailable):
            desk.feed.read_page('w')
        with self.assertRaises(PageUnavailable):
            desk.feed.read_slot(SlotRef('w', 'A'))
        with self.assertRaises(PageUnavailable):
            desk.intake.accept('w')
        with self.assertRaises(PageUnavailable):
            desk.intake.accept('w', ('A',))
        self.assertEqual(desk.status.page_header('w'), header)

    def test_pages_advance_independently(self):
        desk = self.desk
        desk.feed.feed_windowed(make_page('w1', 'W', ('A', 1, 'VA', b'7')), {'A': {2}})
        desk.feed.feed_windowed(make_page('w2', 'W', ('B', 2, 'VB', b'11')), {'B': {1, 3}})
        desk.feed.book('ta', SlotRef('w1', 'A'))
        desk.feed.book('tb', SlotRef('w2', 'B'))
        self.assertIsNone(desk.feed.tick('w1', 1))
        self.assertEqual(desk.feed.tick('w2', 1), 'tb')
        self.assertEqual(desk.feed.tick('w1', 2), 'ta')
        self.assertIsNone(desk.feed.tick('w2', 2))
        self.assertIsNone(desk.feed.tick('w2', 3))


class BookingTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.desk.lines.register('W', 3, 2)
        self.desk.feed.feed_windowed(windowed_page(), {'A': {1, 2}, 'B': {1}})

    def test_booking_identity_rules(self):
        desk = self.desk
        ref_a, ref_b = SlotRef('w', 'A'), SlotRef('w', 'B')
        for bad in ('', '  ', 1, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                desk.feed.book(bad, ref_a)
        with self.assertRaises(ValueError):
            desk.feed.book('t1', SlotRef('w', 'C'))
        with self.assertRaises(ValueError):
            desk.feed.book('t1', SlotRef('nope', 'A'))
        desk.feed.feed(make_page('plain', 'W', ('A', 3, 'VA', b'7')))
        with self.assertRaises(ValueError):
            desk.feed.book('t1', SlotRef('plain', 'A'))
        self.assertEqual(desk.feed.book('t1', ref_a), 'accepted')
        self.assertEqual(desk.feed.book('t1', ref_a), 'accepted')
        with self.assertRaises(ValueError):
            desk.feed.book('t1', ref_b)
        with self.assertRaises(ValueError):
            desk.feed.book('t2', ref_a)
        self.assertEqual(desk.feed.book('t2', ref_b), 'accepted')

    def test_booking_capacity(self):
        desk = self.desk
        desk.feed.feed_windowed(
            make_page('w2', 'W', ('A', 3, 'VA', b'7'), ('B', 4, 'VB', b'11'), ('C', 5, 'VC', b'13')),
            {'A': {1}, 'B': {2}, 'C': {2}})
        self.assertEqual(desk.feed.book('ta', SlotRef('w2', 'A')), 'accepted')
        self.assertEqual(desk.feed.book('tb', SlotRef('w2', 'B')), 'accepted')
        self.assertEqual(desk.feed.book('tc', SlotRef('w2', 'C')), 'unavailable')
        self.assertEqual(desk.feed.book('td', SlotRef('w2', 'C')), 'unavailable')
        self.assertEqual(desk.feed.tick('w2', 1), 'ta')
        self.assertEqual(desk.feed.tick('w2', 2), 'tb')
        self.assertEqual(desk.feed.book('tc', SlotRef('w2', 'C')), 'unavailable')

    def test_elapsed_beats_not_reusable(self):
        desk = self.desk
        desk.feed.feed_windowed(
            make_page('w2', 'W', ('A', 3, 'VA', b'7'), ('B', 4, 'VB', b'11')),
            {'A': {2}, 'B': {1}})
        self.assertEqual(desk.feed.book('ta', SlotRef('w2', 'A')), 'accepted')
        self.assertIsNone(desk.feed.tick('w2', 1))
        self.assertEqual(desk.feed.book('tb', SlotRef('w2', 'B')), 'unavailable')
        self.assertEqual(desk.feed.tick('w2', 2), 'ta')

    def test_tick_rules(self):
        desk = self.desk
        desk.feed.feed_windowed(make_page('w1', 'W', ('A', 3, 'VA', b'7')), {'A': {2}})
        desk.feed.feed(make_page('plain', 'W', ('B', 4, 'VB', b'11')))
        with self.assertRaises(ValueError):
            desk.feed.tick('plain', 1)
        with self.assertRaises(ValueError):
            desk.feed.tick('nope', 1)
        for bad in (0, -1, True, '1', 1.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                desk.feed.tick('w1', bad)
        with self.assertRaises(ValueError):
            desk.feed.tick('w1', 3)
        self.assertEqual(desk.feed.book('ta', SlotRef('w1', 'A')), 'accepted')
        with self.assertRaises(ValueError):
            desk.feed.tick('w1', 2)
        self.assertIsNone(desk.feed.tick('w1', 1))
        self.assertIsNone(desk.feed.tick('w1', 1))
        self.assertEqual(desk.feed.tick('w1', 2), 'ta')
        self.assertEqual(desk.feed.tick('w1', 2), 'ta')
        with self.assertRaises(ValueError):
            desk.feed.tick('w1', 3)

    def test_state_survives_reopen(self):
        desk = self.desk
        desk.feed.book('ta', SlotRef('w', 'A'))
        desk.feed.book('tb', SlotRef('w', 'B'))
        self.assertEqual(desk.feed.tick('w', 1), 'tb')
        desk = self.reopen()
        self.assertEqual(desk.feed.tick('w', 1), 'tb')
        self.assertEqual(desk.feed.tick('w', 2), 'ta')
        desk = self.reopen()
        self.assertEqual(desk.feed.read_terminal('ta').beat, 2)
        self.assertEqual(desk.feed.read_terminal('tb').beat, 1)


class WindowReadTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.desk.lines.register('W', 3, 2)
        self.desk.feed.feed_windowed(windowed_page(), {'A': {1, 2}, 'B': {1}})
        self.desk.feed.book('ta', SlotRef('w', 'A'))
        self.desk.feed.book('tb', SlotRef('w', 'B'))

    def read_both(self):
        self.desk.feed.tick('w', 1)
        self.desk.feed.tick('w', 2)
        return self.desk.feed.read_terminal('ta'), self.desk.feed.read_terminal('tb')

    def test_read_terminal(self):
        desk = self.desk
        self.assertIsNone(desk.feed.read_terminal('tz'))
        self.assertIsNone(desk.feed.read_terminal('ta'))
        read_a, read_b = self.read_both()
        self.assertEqual(read_a, WindowRead('ta', RawSlot(SlotRef('w', 'A'), 1, 'VA', b'7'), 2))
        self.assertEqual(read_b, WindowRead('tb', RawSlot(SlotRef('w', 'B'), 2, 'VB', b'11'), 1))
        self.assertEqual(desk.feed.read_terminal('ta'), read_a)
        desk.intake.collect('ta')
        desk.intake.collect('tb')
        self.assertIs(desk.retirement.retire('w'), RetirementState.CONFIRMED)
        self.assertIsNone(desk.feed.read_terminal('ta'))
        desk.device.stop()
        with self.assertRaises(DeviceUnavailable):
            desk.feed.read_terminal('tb')

    def test_collect_and_replay(self):
        desk = self.desk
        self.assertEqual(desk.intake.collect('ta'), 'pending')
        self.assertEqual(desk.intake.collect('tz'), 'pending')
        read_a, read_b = self.read_both()
        with self.assertRaises(EvidenceMismatch):
            desk.intake.collect('ta', terminal=read_b)
        with self.assertRaises(EvidenceMismatch):
            desk.intake.collect('ta', terminal=replace(read_a, beat=1))
        with self.assertRaises(EvidenceMismatch):
            desk.intake.collect('ta', terminal=replace(read_a, slot=replace(read_a.slot, payload=b'8')))
        self.assertIsNone(desk.intake.lookup_read('ta'))
        self.assertEqual(desk.intake.collect('ta', terminal=read_a), read_a)
        self.assertEqual(desk.depot.get_slot(SlotRef('w', 'A')).payload, b'7')
        self.assertEqual(desk.intake.collect('ta'), read_a)
        self.assertEqual(desk.intake.collect('ta', terminal=read_a), read_a)
        with self.assertRaises(EvidenceMismatch):
            desk.intake.collect('ta', terminal=replace(read_a, beat=1))
        self.assertEqual(desk.intake.lookup_read('ta'), read_a)
        self.assertIsNone(desk.intake.lookup_read('tb'))

    def test_collect_commit_failure_and_retry(self):
        desk = self.desk
        read_a, read_b = self.read_both()
        desk.intake.fail_next.add('w')
        with self.assertRaises(CaptureCommitFailed):
            desk.intake.collect('tb', terminal=read_b)
        self.assertIsNone(desk.intake.lookup_read('tb'))
        self.assertIsNone(desk.depot.get_slot(SlotRef('w', 'B')))
        desk = self.reopen()
        self.assertEqual(desk.intake.collect('tb', terminal=read_b), read_b)
        self.assertEqual(desk.intake.lookup_read('tb'), read_b)
        self.assertEqual(desk.depot.get_slot(SlotRef('w', 'B')).payload, b'11')

    def test_offline_claim(self):
        desk = self.desk
        read_a, read_b = self.read_both()
        desk.intake.collect('ta')
        desk.intake.collect('tb')
        desk = self.reopen()
        desk.device.stop()
        self.assertEqual(desk.intake.lookup_read('ta'), read_a)
        self.assertEqual(desk.intake.lookup_read('tb'), read_b)
        self.assertEqual(desk.intake.collect('tb'), read_b)
        with self.assertRaises(DeviceUnavailable):
            desk.feed.read_terminal('ta')

    def test_retirement_requires_collected_slots(self):
        desk = self.desk
        self.assertIs(desk.retirement.retire('w'), RetirementState.INCOMPLETE)
        self.read_both()
        self.assertIs(desk.retirement.retire('w'), RetirementState.INCOMPLETE)
        desk.intake.collect('ta')
        self.assertIs(desk.retirement.retire('w'), RetirementState.INCOMPLETE)
        desk.intake.collect('tb')
        self.assertIs(desk.retirement.retire('w'), RetirementState.CONFIRMED)

    def test_publish_in_line_order(self):
        desk = self.desk
        self.read_both()
        segment = DeliverySegment(2, ('w',))
        with self.assertRaises(ContinuityGap):
            desk.publisher.publish('W', segment)
        desk.intake.collect('tb')
        with self.assertRaises(ContinuityGap):
            desk.publisher.publish('W', segment)
        desk.intake.collect('ta')
        result = desk.publisher.publish('W', segment)
        self.assertEqual(summaries(result.readings),
                         [('VA', 1, 'w', 'A', 21, 2), ('VB', 2, 'w', 'B', 33, 2)])
        self.assertEqual(desk.publisher.publish('W', segment), result)
