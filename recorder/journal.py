import sqlite3

from recorder.pages import PageHeader, PhysicalPage, RawSlot, SlotEvidence, SlotRef


class RecorderJournal:
    """Device storage. Feed and reclaim own the write transaction boundaries."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS recorder_pages (
                page_number INTEGER PRIMARY KEY,
                page_id TEXT NOT NULL UNIQUE,
                line TEXT NOT NULL REFERENCES lines(line_id),
                erase_count INTEGER NOT NULL DEFAULT 0 CHECK (erase_count IN (0, 1))
            );
            CREATE INDEX IF NOT EXISTS recorder_by_line ON recorder_pages(line, page_number);
            CREATE TABLE IF NOT EXISTS recorder_slots (
                page_id TEXT NOT NULL REFERENCES recorder_pages(page_id),
                coordinate TEXT NOT NULL,
                line TEXT NOT NULL REFERENCES lines(line_id),
                position INTEGER NOT NULL,
                label TEXT NOT NULL,
                digest TEXT NOT NULL,
                payload BLOB,
                PRIMARY KEY (page_id, coordinate),
                UNIQUE (line, position)
            );
        ''')

    def save_page(self, page: PhysicalPage) -> PageHeader:
        header = page.header()
        self.db.execute('INSERT INTO recorder_pages(page_id, line) VALUES (?, ?)',
                        (page.page_id, page.line))
        self.db.executemany('''
            INSERT INTO recorder_slots VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ((page.page_id, proof.ref.coordinate, page.line, proof.position, proof.label,
              proof.digest, slot.payload) for proof, slot in zip(header.slots, page.slots)))
        return header

    def load_page(self, page_id: str) -> PhysicalPage | None:
        rows = self.db.execute('''
            SELECT p.line, s.coordinate, s.position, s.label, s.payload
            FROM recorder_pages AS p LEFT JOIN recorder_slots AS s ON s.page_id = p.page_id
            WHERE p.page_id = ? AND p.erase_count = 0 ORDER BY s.position
        ''', (page_id,)).fetchall()
        if not rows:
            return None
        slots = tuple(RawSlot(SlotRef(page_id, coordinate), position, label, payload)
                      for _, coordinate, position, label, payload in rows if coordinate is not None)
        return PhysicalPage(page_id, rows[0][0], slots)

    def get_header(self, page_id: str) -> PageHeader | None:
        rows = self.db.execute('''
            SELECT p.line, s.coordinate, s.position, s.label, s.digest
            FROM recorder_pages AS p LEFT JOIN recorder_slots AS s ON s.page_id = p.page_id
            WHERE p.page_id = ? ORDER BY s.position
        ''', (page_id,)).fetchall()
        if not rows:
            return None
        slots = tuple(SlotEvidence(SlotRef(page_id, coordinate), line, position, label, digest)
                      for line, coordinate, position, label, digest in rows if coordinate is not None)
        return PageHeader(page_id, rows[0][0], slots)

    def latest_header(self, line: str) -> PageHeader | None:
        row = self.db.execute('''
            SELECT page_id FROM recorder_pages WHERE line = ? ORDER BY page_number DESC LIMIT 1
        ''', (line,)).fetchone()
        return self.get_header(row[0]) if row is not None else None

    def retire_page(self, page_id: str) -> bool:
        count = self.retirement_count(page_id)
        if count:
            return False
        self.db.execute('UPDATE recorder_slots SET payload = NULL WHERE page_id = ?', (page_id,))
        self.db.execute('UPDATE recorder_pages SET erase_count = erase_count + 1 WHERE page_id = ?',
                        (page_id,))
        return True

    def retirement_count(self, page_id: str) -> int:
        row = self.db.execute('SELECT erase_count FROM recorder_pages WHERE page_id = ?',
                              (page_id,)).fetchone()
        if row is None:
            raise KeyError(page_id)
        return row[0]

    def get_retirement(self, page_id: str) -> bool:
        return bool(self.retirement_count(page_id))
