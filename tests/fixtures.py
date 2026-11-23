from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from delivery.continuity import DeliverySegment
from recorder.pages import PhysicalPage, RawSlot, SlotRef
from review.workbench import Workbench, initialize_workspace, open_workbench
from topology.slots import SlotAssignment


EAST_OLD = SlotRef('p-last', 'S0')
WEST_OLD = SlotRef('p-last', 'S1')
EAST_NEW = SlotRef('e-first', 'S0')
EAST_EXPECTED = [('E7', 1, 'p-last', 'S0', 7, 1), ('E9', 2, 'e-first', 'S0', 9, 1)]
WEST_EXPECTED = [('W11', 1, 'p-last', 'S1', 11, 1)]


def make_page(page_id, line, *entries):
    return PhysicalPage(page_id, line, tuple(RawSlot(SlotRef(page_id, coordinate), position, label, raw)
                                             for coordinate, position, label, raw in entries))


def summaries(entries):
    return [(row.label, row.position, row.source.page_id, row.source.coordinate,
             row.value.numerator, row.value.denominator) for row in entries]


class WorkspaceCase(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory(prefix='vibration-test-'))
        self.path = Path(directory) / 'work.sqlite'
        initialize_workspace(self.path)
        self.desk = self.stack.enter_context(open_workbench(self.path))

    def reopen(self) -> Workbench:
        self.stack.pop_all().close() if False else None
        self.desk.db.close()
        self.desk = self.stack.enter_context(open_workbench(self.path))
        return self.desk


@dataclass(frozen=True)
class SplitFixture:
    terminal: PhysicalPage
    east_page: PhysicalPage
    assignments: tuple[SlotAssignment, ...]
    east_segment: DeliverySegment
    west_segment: DeliverySegment


def make_split(desk: Workbench, *, install=True, parent_history=False) -> SplitFixture:
    for line in ('P', 'east', 'west'):
        desk.lines.register(line, 1, 1)
    first = 1
    if parent_history:
        desk.feed.feed(make_page('p-before', 'P', ('S0', 1, 'P5', b'5')))
        desk.intake.accept('p-before')
        desk.publisher.publish('P', DeliverySegment(1, ('p-before',)))
        first = 2
    terminal = make_page('p-last', 'P', ('S0', first, 'E7', b'7'), ('S1', first + 1, 'W11', b'11'))
    east_page = make_page('e-first', 'east', ('S0', 2, 'E9', b'9'))
    desk.feed.feed(terminal)
    assignments = (SlotAssignment(EAST_OLD, 'east', 1), SlotAssignment(WEST_OLD, 'west', 1))
    if install:
        desk.splits.stop_source('P')
        desk.splits.split('P', 'p-last', ('east', 'west'), assignments)
        desk.feed.feed(east_page)
    return SplitFixture(terminal, east_page, assignments, DeliverySegment(2, ('e-first',)), DeliverySegment(1))


def accept_split(desk: Workbench):
    desk.intake.accept('e-first')
    desk.intake.accept('p-last')


def advance_east(desk: Workbench):
    desk.feed.feed(make_page('e-next', 'east', ('S0', 3, 'E13', b'13')))
    desk.intake.accept('e-next')
    return desk.publisher.publish('east', DeliverySegment(3, ('e-next',)))
