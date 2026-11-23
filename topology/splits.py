from dataclasses import dataclass
import sqlite3
from typing import Iterable

from recorder.journal import RecorderJournal
from recorder.pages import SlotRef
from topology.lines import Line, LineRegistry
from topology.slots import SlotAssignment, assignments_for_line, validate_assignments


@dataclass(frozen=True)
class SplitManifest:
    source: str
    last_page: str
    successors: tuple[str, str]
    assignments: tuple[SlotAssignment, ...]


class SplitRegistry:
    def __init__(self, db: sqlite3.Connection, lines: LineRegistry, journal: RecorderJournal):
        self.db = db
        self.lines = lines
        self.journal = journal

    def initialize(self) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS split_manifests (
                source TEXT PRIMARY KEY REFERENCES lines(line_id),
                last_page TEXT NOT NULL REFERENCES recorder_pages(page_id)
            );
            CREATE TABLE IF NOT EXISTS split_successors (
                successor TEXT PRIMARY KEY REFERENCES lines(line_id),
                source TEXT NOT NULL REFERENCES split_manifests(source),
                route_order INTEGER NOT NULL,
                UNIQUE (source, route_order)
            );
            CREATE TABLE IF NOT EXISTS split_assignments (
                source TEXT NOT NULL REFERENCES split_manifests(source),
                successor TEXT NOT NULL REFERENCES split_successors(successor),
                position INTEGER NOT NULL,
                page_id TEXT NOT NULL,
                coordinate TEXT NOT NULL,
                PRIMARY KEY (source, successor, position)
            );
        ''')

    def stop_source(self, line: str) -> Line:
        return self.lines.mark_stopped(line, self.journal.latest_header)

    def split(self, source: str, last_page: str, successors: tuple[str, str],
              assignments: Iterable[SlotAssignment]) -> SplitManifest:
        successors = tuple(successors)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.lines.get_line(source).terminal_page != last_page:
                raise ValueError('split requires the actual stopped terminal page')
            if self._participates(source):
                raise ValueError(f'line already participates in a split: {source}')
            header = self.journal.latest_header(source)
            if header is None or header.page_id != last_page:
                raise ValueError('terminal page does not match the recorder catalogue')
            checked = validate_assignments(header, successors, assignments)
            for successor in successors:
                line = self.lines.get_line(successor)
                if (successor == source or line.terminal_page is not None
                        or self._participates(successor) or self.journal.latest_header(successor)):
                    raise ValueError(f'successor is already in use: {successor}')
            self.db.execute('INSERT INTO split_manifests VALUES (?, ?)', (source, last_page))
            for route_order, successor in enumerate(successors):
                self.db.execute('INSERT INTO split_successors VALUES (?, ?, ?)',
                                (successor, source, route_order))
                tail = assignments_for_line(checked, successor)
                first_native = tail[-1].position + 1 if tail else 1
                self.lines.set_successor_start(successor, first_native)
            self.db.executemany('INSERT INTO split_assignments VALUES (?, ?, ?, ?, ?)',
                                ((source, item.successor, item.position, item.ref.page_id,
                                  item.ref.coordinate) for item in checked))
        return SplitManifest(source, last_page, successors, checked)

    def _participates(self, line: str) -> bool:
        return self.db.execute('''
            SELECT source FROM split_manifests WHERE source = ?
            UNION ALL SELECT successor FROM split_successors WHERE successor = ?
        ''', (line, line)).fetchone() is not None

    def for_successor(self, line: str) -> SplitManifest | None:
        row = self.db.execute('''
            SELECT m.source, m.last_page FROM split_successors AS s
            JOIN split_manifests AS m ON m.source = s.source WHERE s.successor = ?
        ''', (line,)).fetchone()
        if row is None:
            return None
        source, last_page = row
        successors = tuple(item[0] for item in self.db.execute('''
            SELECT successor FROM split_successors WHERE source = ? ORDER BY route_order
        ''', (source,)))
        assignments = tuple(
            SlotAssignment(SlotRef(page_id, coordinate), successor, position)
            for successor, position, page_id, coordinate in self.db.execute('''
                SELECT a.successor, a.position, a.page_id, a.coordinate
                FROM split_assignments AS a JOIN split_successors AS s ON s.successor = a.successor
                WHERE a.source = ? ORDER BY s.route_order, a.position
            ''', (source,))
        )
        return SplitManifest(source, last_page, successors, assignments)
