import argparse
from contextlib import closing
from dataclasses import asdict
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from capture.depot import CustodyDepot, RetirementState
from capture.intake import CaptureIntake
from capture.recovery import RetirementRecovery
from capture.retirement import RetirementService
from delivery.continuity import ContinuityGap, DeliverySegment
from delivery.publish import DeliveryPublisher, PublicationCommitFailed
from delivery.results import DeliveryStore
from recorder.feed import RecorderFeed
from recorder.journal import RecorderJournal
from recorder.pages import PhysicalPage, RawSlot, SlotRef
from recorder.reclaim import RecorderReclaimer
from recorder.status import DeviceAvailability, RecorderStatus, RetirementOutcome
from review.discrepancies import ReviewExpectation
from review.lookup import ReviewLookup
from review.shift import ShiftReview
from topology.lines import LineRegistry
from topology.slots import SlotAssignment
from topology.splits import SplitRegistry


class Workbench:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute('PRAGMA foreign_keys = ON')
        self.lines = LineRegistry(db)
        self.journal = RecorderJournal(db)
        self.device = DeviceAvailability()
        self.status = RecorderStatus(self.journal, self.device)
        self.feed = RecorderFeed(self.journal, self.status, self.lines)
        self.reclaimer = RecorderReclaimer(self.journal, self.device)
        self.splits = SplitRegistry(db, self.lines, self.journal)
        self.depot = CustodyDepot(db)
        self.intake = CaptureIntake(self.feed, self.depot)
        self.retirement = RetirementService(self.depot, self.status, self.reclaimer)
        self.recovery = RetirementRecovery(self.depot, self.retirement)
        self.results = DeliveryStore(db)
        self.publisher = DeliveryPublisher(self.lines, self.splits, self.depot, self.results)
        self.lookup = ReviewLookup(self.results)
        self.shift = ShiftReview(db, self.lookup)

    def __enter__(self) -> 'Workbench':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.db.close()


def initialize_workspace(path: str | Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        desk = Workbench(db)
        for service in (desk.lines, desk.journal, desk.splits, desk.depot, desk.results, desk.shift):
            service.initialize()
        db.commit()


def open_workbench(path: str | Path) -> Workbench:
    uri = Path(path).absolute().as_uri() + '?mode=rw'
    db = sqlite3.connect(uri, uri=True)
    try:
        return Workbench(db)
    except BaseException:
        db.close()
        raise


def _sample(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / 'samples' / name
    return json.loads(path.read_text(encoding='utf-8'))


def _sample_page(data: dict) -> PhysicalPage:
    page_id = data['page_id']
    slots = tuple(RawSlot(SlotRef(page_id, entry['coordinate']), entry['position'],
                          entry['label'], entry['raw'].encode('ascii')) for entry in data['slots'])
    return PhysicalPage(page_id, data['line'], slots)


def _rows(entries) -> list:
    return [[row.label, row.position, row.source.page_id, row.source.coordinate,
             row.value.numerator, row.value.denominator] for row in entries]


def _check(actual, expected) -> None:
    if actual != expected:
        raise RuntimeError(f'expected {expected!r}, obtained {actual!r}')


def _report(case_id: str, supplied, expected, actual, downstream_expected, downstream_actual,
            paths: list[str], oracle: str) -> None:
    _check(actual, expected)
    _check(downstream_actual, downstream_expected)
    record = dict(case_id=case_id, input=supplied, expected=expected, actual=actual,
                  downstream_expected=downstream_expected, downstream_actual=downstream_actual,
                  code_paths=paths, oracle=oracle)
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False, sort_keys=True))


def _ordinary_demo(desk: Workbench, data: dict, expected: list) -> None:
    page = _sample_page(data['page'])
    desk.lines.register(page.line, *data['ratio'])
    desk.feed.feed(page)
    desk.intake.accept(page.page_id)
    result = desk.publisher.publish(page.line, DeliverySegment(len(expected), (page.page_id,)))
    if result is None:
        raise RuntimeError('ordinary page had no publication')
    historical = desk.lookup.provenance(result.result_id)
    _report('v1', data, expected, _rows(result.readings), expected, _rows(historical),
            ['capture/intake.py', 'delivery/conversion.py', 'delivery/publish.py', 'review/lookup.py'],
            'Multiply 7 and 11 by 3/2 by hand and retain page coordinates A1 and A2.')


def _handoff_demo(desk: Workbench, data: dict):
    terminal = _sample_page(data['terminal'])
    successor = _sample_page(data['early_page'])
    west = data['successors'][1]
    for line in (terminal.line, *data['successors']):
        desk.lines.register(line, 1, 1)
    desk.feed.feed(terminal)
    desk.splits.stop_source(terminal.line)
    assignments = tuple(SlotAssignment(SlotRef(terminal.page_id, entry['coordinate']),
                                       entry['line'], entry['position']) for entry in data['assignments'])
    desk.splits.split(terminal.line, terminal.page_id, tuple(data['successors']), assignments)
    desk.feed.feed(successor)
    desk.intake.accept(successor.page_id)
    east_segment = DeliverySegment(successor.slots[-1].position, (successor.page_id,))
    west_segment = DeliverySegment(data['west_through'])
    try:
        desk.publisher.publish(successor.line, east_segment)
    except ContinuityGap:
        pass
    else:
        raise RuntimeError('early successor page passed the old-tail check')
    east_slot, west_slot = (item.ref for item in assignments)
    desk.feed.paused_slots.add(west_slot)
    desk.intake.accept(terminal.page_id, (east_slot.coordinate,))
    _check(desk.retirement.retire(terminal.page_id), RetirementState.INCOMPLETE)
    _check(desk.status.query_retirement(terminal.page_id), RetirementOutcome.RETAINED)
    desk.feed.paused_slots.remove(west_slot)
    desk.intake.accept(terminal.page_id, (west_slot.coordinate,))
    desk.publisher.fail_next.add(west)
    try:
        desk.publisher.publish(west, west_segment)
    except PublicationCommitFailed:
        _check(desk.results.get_frontier(west), None)
    else:
        raise RuntimeError('west publication did not reach the scheduled failure')
    east_result = desk.publisher.publish(successor.line, east_segment)
    if east_result is None:
        raise RuntimeError('east publication missing')
    desk.reclaimer.lose_reply.add(terminal.page_id)
    _check(desk.retirement.retire(terminal.page_id), RetirementState.PENDING)
    return east_result, east_segment, west_segment


def _review_demo(desk: Workbench, data: dict, expected: dict, east_result, east_segment, west_segment) -> None:
    terminal_id = data['terminal']['page_id']
    east, west = data['successors']
    resumed = desk.recovery.resume()
    _check(resumed, {terminal_id: RetirementState.CONFIRMED})
    desk.device.stop()
    west_result = desk.publisher.publish(west, west_segment)
    if west_result is None:
        raise RuntimeError('west publication missing after recovery')
    results = {east: east_result, west: west_result}
    _check(desk.publisher.publish(east, east_segment), east_result)
    _check(desk.publisher.publish(west, west_segment), west_result)
    desk.shift.begin_batch('next-shift')
    for route, result in results.items():
        checklist = tuple(ReviewExpectation(position, label, SlotRef(page, coordinate), Fraction(n, d))
                          for label, position, page, coordinate, n, d in expected[route])
        desk.shift.register_item('next-shift', route, result.result_id, checklist)
        item = desk.shift.record_review('next-shift', route)
        _check(item.receipt_id, result.receipt.receipt_id)
        _check(item.discrepancies, ())
    actual = {route: _rows(result.readings) for route, result in results.items()}
    reviewed = {item.item_id: _rows(item.actual) for item in desk.shift.get_batch('next-shift').items}
    _report('v7', data, expected, actual, expected, reviewed,
            ['capture/retirement.py', 'capture/recovery.py', 'recorder/reclaim.py',
             'delivery/publish.py', 'review/shift.py'],
            'At unit gain, order the manifest old slots before the successor positions and compare by page and slot.')
    for result in results.values():
        print(json.dumps({'line': result.line, 'readings': _rows(result.readings),
                          'receipt': asdict(result.receipt)}, ensure_ascii=False, sort_keys=True))


def run_demo() -> None:
    ordinary = _sample('ordinary.json')
    split = _sample('split.json')
    expected = _sample('review.json')
    with TemporaryDirectory(prefix='vibration-demo-') as directory:
        path = Path(directory) / 'shift.sqlite'
        initialize_workspace(path)
        with open_workbench(path) as desk:
            _ordinary_demo(desk, ordinary, expected['ordinary'])
            east_result, east_segment, west_segment = _handoff_demo(desk, split)
        with open_workbench(path) as desk:
            _review_demo(desk, split, expected['split'], east_result, east_segment, west_segment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Trackside vibration workbench')
    parser.add_argument('command', choices=('demo',))
    parser.parse_args(argv)
    run_demo()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
