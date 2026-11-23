from enum import Enum
import json
import sqlite3
from typing import Iterable

from capture.evidence import EvidenceMismatch, VerifiedSlot
from recorder.feed import WindowRead
from recorder.pages import PageHeader, RawSlot, SlotEvidence, SlotRef


class RetirementState(Enum):
    INCOMPLETE = 'incomplete'
    PENDING = 'pending'
    CONFIRMED = 'confirmed'


class CustodyDepot:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS custody_pages (
                page_id TEXT PRIMARY KEY,
                line TEXT NOT NULL,
                directory TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custody_slots (
                page_id TEXT NOT NULL REFERENCES custody_pages(page_id),
                coordinate TEXT NOT NULL,
                line TEXT NOT NULL,
                position INTEGER NOT NULL,
                label TEXT NOT NULL,
                payload BLOB NOT NULL,
                digest TEXT NOT NULL,
                PRIMARY KEY (page_id, coordinate)
            );
            CREATE TABLE IF NOT EXISTS custody_retirements (
                page_id TEXT PRIMARY KEY REFERENCES custody_pages(page_id),
                state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed'))
            );
            CREATE TABLE IF NOT EXISTS custody_window_reads (
                ticket TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                coordinate TEXT NOT NULL,
                beat INTEGER NOT NULL
            );
        ''')

    def get_header(self, page_id: str) -> PageHeader | None:
        row = self.db.execute('SELECT line, directory FROM custody_pages WHERE page_id = ?',
                              (page_id,)).fetchone()
        if row is None:
            return None
        line, directory = row
        slots = tuple(SlotEvidence(SlotRef(page_id, coordinate), line, position, label, digest)
                      for coordinate, position, label, digest in json.loads(directory))
        return PageHeader(page_id, line, slots)

    def save_verified(self, header: PageHeader, slots: Iterable[VerifiedSlot]) -> None:
        """Write verified facts inside the intake transaction without replacing prior evidence."""
        known = self.get_header(header.page_id)
        if known is None:
            directory = [(slot.ref.coordinate, slot.position, slot.label, slot.digest)
                         for slot in header.slots]
            self.db.execute('INSERT INTO custody_pages VALUES (?, ?, ?)',
                            (header.page_id, header.line, json.dumps(directory)))
        elif known != header:
            raise EvidenceMismatch(f'conflicting device directory: {header.page_id}')
        proofs = {proof.ref: proof for proof in header.slots}
        for slot in slots:
            if proofs.get(slot.ref) != slot.evidence():
                raise EvidenceMismatch(f'original does not match custody directory: {slot.ref}')
            existing = self.get_slot(slot.ref)
            if existing is None:
                self.db.execute('INSERT INTO custody_slots VALUES (?, ?, ?, ?, ?, ?, ?)',
                                (slot.ref.page_id, slot.ref.coordinate, slot.line, slot.position,
                                 slot.label, slot.payload, slot.digest))
            elif existing != slot:
                raise EvidenceMismatch(f'conflicting original at {slot.ref}')

    def get_slot(self, ref: SlotRef) -> VerifiedSlot | None:
        row = self.db.execute('''
            SELECT line, position, label, payload, digest FROM custody_slots
            WHERE page_id = ? AND coordinate = ?
        ''', (ref.page_id, ref.coordinate)).fetchone()
        return VerifiedSlot(ref, *row) if row is not None else None

    def slots_for_page(self, page_id: str) -> tuple[VerifiedSlot, ...]:
        rows = self.db.execute('''
            SELECT coordinate, line, position, label, payload, digest FROM custody_slots
            WHERE page_id = ? ORDER BY position
        ''', (page_id,))
        return tuple(VerifiedSlot(SlotRef(page_id, coordinate), line, position, label, payload, digest)
                     for coordinate, line, position, label, payload, digest in rows)

    def get_retirement(self, page_id: str) -> RetirementState | None:
        row = self.db.execute('SELECT state FROM custody_retirements WHERE page_id = ?',
                              (page_id,)).fetchone()
        return RetirementState(row[0]) if row is not None else None

    def save_retirement(self, page_id: str, state: RetirementState) -> None:
        if state is RetirementState.INCOMPLETE:
            raise ValueError('incomplete custody has no retirement record')
        self.db.execute('''
            INSERT INTO custody_retirements VALUES (?, ?)
            ON CONFLICT(page_id) DO UPDATE SET state = excluded.state
            WHERE custody_retirements.state = 'pending'
        ''', (page_id, state.value))

    def pending_retirements(self) -> tuple[str, ...]:
        return tuple(row[0] for row in self.db.execute('''
            SELECT page_id FROM custody_retirements WHERE state = 'pending' ORDER BY page_id
        '''))

    def save_window_read(self, read: WindowRead) -> None:
        """Commit the booking receipt inside the same transaction as its original."""
        self.db.execute('INSERT INTO custody_window_reads VALUES (?, ?, ?, ?)',
                        (read.ticket, read.slot.ref.page_id, read.slot.ref.coordinate, read.beat))

    def get_window_read(self, ticket: str) -> WindowRead | None:
        row = self.db.execute('''
            SELECT page_id, coordinate, beat FROM custody_window_reads WHERE ticket = ?
        ''', (ticket,)).fetchone()
        if row is None:
            return None
        page_id, coordinate, beat = row
        slot = self.get_slot(SlotRef(page_id, coordinate))
        if slot is None:
            return None
        raw = RawSlot(slot.ref, slot.position, slot.label, slot.payload)
        return WindowRead(ticket, raw, beat)
