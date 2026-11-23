from dataclasses import dataclass
import sqlite3
from typing import Callable, Protocol

from topology.profiles import ConversionProfile, build_profile


class _TerminalPage(Protocol):
    page_id: str


@dataclass(frozen=True)
class Line:
    line_id: str
    profile: ConversionProfile
    terminal_page: str | None
    first_native_position: int


class LineRegistry:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS lines (
                line_id TEXT PRIMARY KEY,
                numerator TEXT NOT NULL,
                denominator TEXT NOT NULL,
                terminal_page TEXT,
                first_native_position INTEGER NOT NULL DEFAULT 1
            )
        ''')

    def register(self, line: str, numerator: int, denominator: int) -> Line:
        if not isinstance(line, str) or not line.strip():
            raise ValueError('line ID is required')
        profile = build_profile(numerator, denominator)
        with self.db:
            changed = self.db.execute('''
                INSERT INTO lines(line_id, numerator, denominator) VALUES (?, ?, ?)
                ON CONFLICT(line_id) DO NOTHING
            ''', (line, hex(profile.numerator), hex(profile.denominator))).rowcount
            if not changed:
                raise ValueError(f'line already registered: {line}')
        return Line(line, profile, None, 1)

    def get_line(self, line: str) -> Line:
        row = self.db.execute('''
            SELECT numerator, denominator, terminal_page, first_native_position
            FROM lines WHERE line_id = ?
        ''', (line,)).fetchone()
        if row is None:
            raise KeyError(line)
        numerator, denominator, terminal_page, first_native_position = row
        profile = ConversionProfile(int(numerator, 16), int(denominator, 16))
        return Line(line, profile, terminal_page, first_native_position)

    def mark_stopped(self, line: str, latest_header: Callable[[str], _TerminalPage | None]) -> Line:
        """Resolve the terminal page while holding the same write lock as recorder writes."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get_line(line)
            if current.terminal_page is not None:
                return current
            header = latest_header(line)
            if header is None:
                raise ValueError(f'line has no terminal page: {line}')
            self.db.execute('UPDATE lines SET terminal_page = ? WHERE line_id = ?',
                            (header.page_id, line))
            return self.get_line(line)

    def set_successor_start(self, line: str, first_native_position: int) -> None:
        """Set the reservation inside the transaction that fixes the split manifest."""
        self.db.execute('UPDATE lines SET first_native_position = ? WHERE line_id = ?',
                        (first_native_position, line))
