from dataclasses import dataclass
from fractions import Fraction
import sqlite3

from delivery.conversion import ConvertedReading
from delivery.receipts import PublicationKey, Receipt
from recorder.pages import SlotRef


@dataclass(frozen=True)
class DeliveryResult:
    result_id: str
    line: str
    readings: tuple[ConvertedReading, ...]
    receipt: Receipt


class DeliveryStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS delivery_frontiers (
                line TEXT PRIMARY KEY REFERENCES lines(line_id),
                position INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_results (
                result_id TEXT PRIMARY KEY,
                line TEXT NOT NULL REFERENCES lines(line_id),
                request_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS delivery_readings (
                result_id TEXT NOT NULL REFERENCES delivery_results(result_id),
                position INTEGER NOT NULL,
                label TEXT NOT NULL,
                page_id TEXT NOT NULL,
                coordinate TEXT NOT NULL,
                numerator TEXT NOT NULL,
                denominator TEXT NOT NULL,
                PRIMARY KEY (result_id, position)
            );
            CREATE TABLE IF NOT EXISTS delivery_receipts (
                receipt_id TEXT PRIMARY KEY,
                result_id TEXT NOT NULL UNIQUE REFERENCES delivery_results(result_id),
                start INTEGER NOT NULL,
                through INTEGER NOT NULL,
                unit TEXT NOT NULL
            );
        ''')

    def get_frontier(self, line: str) -> int | None:
        row = self.db.execute('SELECT position FROM delivery_frontiers WHERE line = ?',
                              (line,)).fetchone()
        return row[0] if row is not None else None

    def record_publication(self, key: PublicationKey, receipt: Receipt,
                           readings: tuple[ConvertedReading, ...]) -> DeliveryResult:
        self.db.execute('INSERT INTO delivery_results VALUES (?, ?, ?)',
                        (receipt.result_id, key.line, key.encode()))
        self.db.executemany('INSERT INTO delivery_readings VALUES (?, ?, ?, ?, ?, ?, ?)',
                            ((receipt.result_id, row.position, row.label, row.source.page_id,
                              row.source.coordinate, hex(row.value.numerator), hex(row.value.denominator))
                             for row in readings))
        self.db.execute('''
            INSERT INTO delivery_frontiers VALUES (?, ?)
            ON CONFLICT(line) DO UPDATE SET position = excluded.position
        ''', (key.line, receipt.through))
        self.db.execute('INSERT INTO delivery_receipts VALUES (?, ?, ?, ?, ?)',
                        (receipt.receipt_id, receipt.result_id, receipt.start, receipt.through, receipt.unit))
        return DeliveryResult(receipt.result_id, key.line, readings, receipt)

    def get_result(self, result_id: str) -> DeliveryResult | None:
        row = self.db.execute('''
            SELECT r.line, s.receipt_id, s.start, s.through, s.unit
            FROM delivery_results AS r JOIN delivery_receipts AS s ON s.result_id = r.result_id
            WHERE r.result_id = ?
        ''', (result_id,)).fetchone()
        if row is None:
            return None
        line, receipt_id, start, through, unit = row
        entries = self.db.execute('''
            SELECT position, label, page_id, coordinate, numerator, denominator
            FROM delivery_readings WHERE result_id = ? ORDER BY position
        ''', (result_id,))
        readings = tuple(ConvertedReading(position, label, SlotRef(page_id, coordinate),
                                          Fraction(int(numerator, 16), int(denominator, 16)))
                         for position, label, page_id, coordinate, numerator, denominator in entries)
        return DeliveryResult(result_id, line, readings, Receipt(receipt_id, result_id, line, start, through, unit))

    def find_by_request(self, key: PublicationKey) -> DeliveryResult | None:
        row = self.db.execute('SELECT result_id FROM delivery_results WHERE request_key = ?',
                              (key.encode(),)).fetchone()
        return self.get_result(row[0]) if row is not None else None
