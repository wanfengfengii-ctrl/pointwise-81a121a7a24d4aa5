import sqlite3

from capture.depot import CustodyDepot
from delivery.continuity import DeliverySegment, plan_segment
from delivery.conversion import convert_reading
from delivery.receipts import build_receipt, publication_key
from delivery.results import DeliveryResult, DeliveryStore
from topology.lines import LineRegistry
from topology.splits import SplitRegistry


class PublicationCommitFailed(sqlite3.OperationalError):
    pass


class DeliveryPublisher:
    def __init__(self, lines: LineRegistry, splits: SplitRegistry, depot: CustodyDepot,
                 store: DeliveryStore):
        self.lines = lines
        self.splits = splits
        self.depot = depot
        self.store = store
        self.fail_next: set[str] = set()

    def publish(self, line: str, segment: DeliverySegment) -> DeliveryResult | None:
        key = publication_key(line, segment)
        db = self.store.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            profile = self.lines.get_line(line).profile
            prior = self.store.find_by_request(key)
            if prior is not None:
                return prior
            frontier = self.store.get_frontier(line)
            manifest = self.splits.for_successor(line)
            originals = plan_segment(line, frontier, segment, manifest, self.depot)
            if not originals:
                return None
            readings = tuple(convert_reading(position, slot, profile) for position, slot in originals)
            receipt = build_receipt(key, readings[0].position)
            result = self.store.record_publication(key, receipt, readings)
            if line in self.fail_next:
                self.fail_next.remove(line)
                raise PublicationCommitFailed(line)
        return result
