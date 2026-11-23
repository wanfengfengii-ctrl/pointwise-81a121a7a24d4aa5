from dataclasses import astuple, dataclass
from fractions import Fraction
import json
import sqlite3
from typing import Iterable

from recorder.pages import SlotRef, validate_position
from review.discrepancies import Discrepancy, ReviewExpectation, compare_checklist
from review.lookup import ReviewLookup
from review.provenance import ProvenanceEntry, list_provenance


@dataclass(frozen=True)
class ReviewItem:
    item_id: str
    result_id: str
    checklist: tuple[ReviewExpectation, ...]
    receipt_id: str | None
    actual: tuple[ProvenanceEntry, ...]
    discrepancies: tuple[Discrepancy, ...] | None


@dataclass(frozen=True)
class ReviewBatch:
    batch_id: str
    items: tuple[ReviewItem, ...]


def _encode(checklist: tuple[ReviewExpectation, ...]) -> str:
    rows = []
    seen = set()
    for row in checklist:
        validate_position(row.position)
        if row.position in seen:
            raise ValueError(f'repeated checklist position: {row.position}')
        seen.add(row.position)
        rows.append((row.position, row.label, row.source.page_id, row.source.coordinate,
                     hex(row.value.numerator), hex(row.value.denominator)))
    return json.dumps(rows)


def _decode(encoded: str) -> tuple[ReviewExpectation, ...]:
    return tuple(ReviewExpectation(position, label, SlotRef(page, coordinate),
                                   Fraction(int(numerator, 16), int(denominator, 16)))
                 for position, label, page, coordinate, numerator, denominator in json.loads(encoded))


class ShiftReview:
    def __init__(self, db: sqlite3.Connection, lookup: ReviewLookup):
        self.db = db
        self.lookup = lookup

    def initialize(self) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS review_batches (batch_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS review_items (
                batch_id TEXT NOT NULL REFERENCES review_batches(batch_id),
                item_id TEXT NOT NULL,
                result_id TEXT NOT NULL,
                checklist TEXT NOT NULL,
                receipt_id TEXT REFERENCES delivery_receipts(receipt_id),
                discrepancies TEXT,
                PRIMARY KEY (batch_id, item_id)
            );
        ''')

    def begin_batch(self, batch_id: str) -> ReviewBatch:
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError('batch ID is required')
        with self.db:
            self.db.execute('INSERT INTO review_batches VALUES (?) ON CONFLICT DO NOTHING', (batch_id,))
        return self.get_batch(batch_id)

    def register_item(self, batch_id: str, item_id: str, result_id: str,
                      checklist: Iterable[ReviewExpectation]) -> None:
        if any(not isinstance(identifier, str) or not identifier.strip()
               for identifier in (item_id, result_id)):
            raise ValueError('item and result IDs are required')
        encoded = _encode(tuple(checklist))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._require_batch(batch_id)
            prior = self.db.execute('''
                SELECT result_id, checklist FROM review_items WHERE batch_id = ? AND item_id = ?
            ''', (batch_id, item_id)).fetchone()
            if prior is not None and prior != (result_id, encoded):
                raise ValueError(f'conflicting checklist for {item_id}')
            if prior is None:
                self.db.execute('''
                    INSERT INTO review_items(batch_id, item_id, result_id, checklist) VALUES (?, ?, ?, ?)
                ''', (batch_id, item_id, result_id, encoded))

    def _require_batch(self, batch_id: str) -> None:
        row = self.db.execute('SELECT batch_id FROM review_batches WHERE batch_id = ?',
                              (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)

    def _as_item(self, row: tuple) -> ReviewItem:
        item_id, result_id, encoded, receipt_id, discrepancies = row
        actual = self.lookup.provenance(result_id) if receipt_id is not None else ()
        differences = (tuple(Discrepancy(*values) for values in json.loads(discrepancies))
                       if discrepancies is not None else None)
        return ReviewItem(item_id, result_id, _decode(encoded), receipt_id, actual, differences)

    def record_review(self, batch_id: str, item_id: str) -> ReviewItem:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('''
                SELECT item_id, result_id, checklist, receipt_id, discrepancies
                FROM review_items WHERE batch_id = ? AND item_id = ?
            ''', (batch_id, item_id)).fetchone()
            if row is None:
                raise KeyError((batch_id, item_id))
            item = self._as_item(row)
            if item.receipt_id is not None:
                return item
            result = self.lookup.review(item.result_id)
            actual = list_provenance(result)
            differences = compare_checklist(item.checklist, actual)
            receipt_id = result.receipt.receipt_id
            self.db.execute('''
                UPDATE review_items SET receipt_id = ?, discrepancies = ?
                WHERE batch_id = ? AND item_id = ?
            ''', (receipt_id, json.dumps([astuple(difference) for difference in differences]), batch_id, item_id))
        return ReviewItem(item_id, item.result_id, item.checklist, receipt_id, actual, differences)

    def get_batch(self, batch_id: str) -> ReviewBatch:
        self._require_batch(batch_id)
        rows = self.db.execute('''
            SELECT item_id, result_id, checklist, receipt_id, discrepancies
            FROM review_items WHERE batch_id = ? ORDER BY item_id
        ''', (batch_id,))
        return ReviewBatch(batch_id, tuple(self._as_item(row) for row in rows))
