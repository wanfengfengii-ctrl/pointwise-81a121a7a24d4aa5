from capture.depot import RetirementState
from delivery.continuity import ContinuityGap, DeliverySegment
from delivery.publish import PublicationCommitFailed
from delivery.receipts import build_receipt, publication_key
from recorder.feed import SlotPaused
from recorder.pages import SlotRef
from recorder.status import PageUnavailable, RetirementOutcome
from review.lookup import ResultUnavailable
from tests.fixtures import (EAST_EXPECTED, EAST_OLD, WEST_EXPECTED, WEST_OLD, WorkspaceCase,
                            accept_split, advance_east, make_page, make_split, summaries)
from topology.slots import SlotAssignment


class SplitTests(WorkspaceCase):
    def test_invalid_manifest(self):
        desk = self.desk
        fixture = make_split(desk, install=False, parent_history=True)
        old = desk.publisher.publish('P', DeliverySegment(1, ('p-before',)))
        desk.splits.stop_source('P')
        overlapping = fixture.assignments + (SlotAssignment(EAST_OLD, 'west', 2),)
        with self.assertRaises(ValueError):
            desk.splits.split('P', 'p-last', ('east', 'west'), overlapping)
        self.assertIsNone(desk.splits.for_successor('east'))
        self.assertIsNone(desk.splits.for_successor('west'))
        self.assertEqual(desk.lookup.review(old.result_id), old)
        self.assertEqual(summaries(old.readings), [('P5', 1, 'p-before', 'S0', 5, 1)])

    def test_manifest_coverage(self):
        desk = self.desk
        fixture = make_split(desk, install=False)
        desk.splits.stop_source('P')
        cases = (
            fixture.assignments[:1],
            (fixture.assignments[0], SlotAssignment(SlotRef('outside', 'S1'), 'west', 1)),
            (fixture.assignments[0], SlotAssignment(WEST_OLD, 'east', 1)),
        )
        for assignments in cases:
            with self.subTest(assignments=assignments), self.assertRaises(ValueError):
                desk.splits.split('P', 'p-last', ('east', 'west'), assignments)
        self.assertIsNone(desk.splits.for_successor('east'))
        expected = desk.splits.split('P', 'p-last', ('east', 'west'), fixture.assignments)
        self.assertEqual(desk.splits.for_successor('west'), expected)
        with self.assertRaises(ValueError):
            desk.splits.split('P', 'p-last', ('east', 'west'), fixture.assignments)

    def test_stop_boundary(self):
        desk = self.desk
        fixture = make_split(desk, install=False, parent_history=True)
        desk.lines.register('unused', 1, 1)
        with self.assertRaises(ValueError):
            desk.splits.stop_source('unused')
        self.assertIsNone(desk.lines.get_line('unused').terminal_page)
        with self.assertRaises(ValueError):
            desk.splits.split('P', 'p-last', ('east', 'west'), fixture.assignments)
        stopped = desk.splits.stop_source('P')
        self.assertEqual(stopped.terminal_page, 'p-last')
        self.assertEqual(desk.splits.stop_source('P'), stopped)
        with self.assertRaises(ValueError):
            desk.feed.feed(make_page('late', 'P', ('S0', 4, 'late', b'17')))
        with self.assertRaises(PageUnavailable):
            desk.status.page_header('late')
        with self.assertRaises(ValueError):
            desk.splits.split('P', 'p-before', ('east', 'west'), fixture.assignments)
        self.assertEqual(desk.journal.latest_header('P').page_id, 'p-last')

    def test_successor_first(self):
        desk = self.desk
        fixture = make_split(desk)
        self.assertEqual(desk.intake.accept('e-first')[0].payload, b'9')
        with self.assertRaises(ContinuityGap):
            desk.publisher.publish('east', fixture.east_segment)
        self.assertIsNone(desk.results.get_frontier('east'))
        self.assertIsNone(desk.results.find_by_request(publication_key('east', fixture.east_segment)))
        desk.feed.paused_slots.add(WEST_OLD)
        desk.intake.accept('p-last', ('S0',))
        east = desk.publisher.publish('east', fixture.east_segment)
        self.assertEqual(summaries(desk.lookup.review(east.result_id).readings), EAST_EXPECTED)
        self.assertIsNone(desk.depot.get_slot(WEST_OLD))
        self.assertEqual(desk.results.get_frontier('east'), 2)

    def test_followup_segment(self):
        desk = self.desk
        fixture = make_split(desk)
        accept_split(desk)
        opening = desk.publisher.publish('east', fixture.east_segment)
        desk.feed.feed(make_page('later', 'east', ('S0', 4, 'E17', b'17')))
        desk.intake.accept('later')
        with self.assertRaises(ContinuityGap):
            desk.publisher.publish('east', DeliverySegment(4, ('later',)))
        self.assertEqual(desk.results.get_frontier('east'), 2)
        next_result = advance_east(desk)
        self.assertEqual(summaries(next_result.readings), [('E13', 3, 'e-next', 'S0', 13, 1)])
        later = desk.publisher.publish('east', DeliverySegment(4, ('later',)))
        self.assertEqual(summaries(later.readings), [('E17', 4, 'later', 'S0', 17, 1)])
        self.assertEqual(desk.lookup.review(opening.result_id), opening)

    def test_incomplete_custody(self):
        desk = self.desk
        make_split(desk)
        desk.feed.paused_slots.add(WEST_OLD)
        desk.intake.accept('p-last', ('S0',))
        with self.assertRaises(SlotPaused):
            desk.intake.accept('p-last')
        self.assertIsNone(desk.depot.get_slot(WEST_OLD))
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.INCOMPLETE)
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETAINED)
        self.assertIsNone(desk.depot.get_retirement('p-last'))
        desk.feed.paused_slots.remove(WEST_OLD)
        self.assertEqual(desk.feed.read_slot(WEST_OLD).payload, b'11')
        desk.intake.accept('p-last', ('S1',))
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.CONFIRMED)
        self.assertIs(desk.status.query_retirement('p-last'), RetirementOutcome.RETIRED)
        with self.assertRaises(PageUnavailable):
            desk.feed.read_page('p-last')
        self.assertEqual(desk.depot.get_slot(WEST_OLD).payload, b'11')

    def test_west_publish_failure(self):
        desk = self.desk
        fixture = make_split(desk)
        accept_split(desk)
        desk.publisher.fail_next.add('west')
        with self.assertRaises(PublicationCommitFailed):
            desk.publisher.publish('west', fixture.west_segment)
        east = desk.publisher.publish('east', fixture.east_segment)
        self.assertEqual(summaries(desk.lookup.provenance(east.result_id)), EAST_EXPECTED)
        self.assertIsNone(desk.results.get_frontier('west'))
        self.assertIsNone(desk.results.find_by_request(publication_key('west', fixture.west_segment)))
        self.assertIs(desk.retirement.retire('p-last'), RetirementState.CONFIRMED)
        west = desk.publisher.publish('west', fixture.west_segment)
        self.assertEqual(summaries(west.readings), WEST_EXPECTED)
        self.assertEqual(desk.publisher.publish('west', fixture.west_segment), west)

    def test_west_commit_rollback(self):
        desk = self.desk
        fixture = make_split(desk)
        accept_split(desk)
        desk.publisher.fail_next.add('west')
        with self.assertRaises(PublicationCommitFailed):
            desk.publisher.publish('west', fixture.west_segment)
        desk = self.reopen()
        for table in ('delivery_results', 'delivery_readings', 'delivery_frontiers', 'delivery_receipts'):
            self.assertEqual(desk.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        candidate = build_receipt(publication_key('west', fixture.west_segment), 1)
        with self.assertRaises(ResultUnavailable):
            desk.lookup.review(candidate.result_id)
        east = desk.publisher.publish('east', fixture.east_segment)
        self.assertEqual(summaries(east.readings), EAST_EXPECTED)
        west = desk.publisher.publish('west', fixture.west_segment)
        self.assertEqual(west.receipt, candidate)
        self.assertEqual(summaries(west.readings), WEST_EXPECTED)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0], 2)
