import sqlite3
from dataclasses import dataclass

from recorder.journal import RecorderJournal
from recorder.pages import PageHeader, PhysicalPage, RawSlot, SlotRef, validate_position
from recorder.status import DeviceUnavailable, PageUnavailable, RecorderStatus
from topology.lines import LineRegistry


class SlotPaused(DeviceUnavailable):
    pass


@dataclass(frozen=True)
class WindowRead:
    """Terminal fact of one executed window read: ticket, original slot, actual beat."""

    ticket: str
    slot: RawSlot
    beat: int


def _allocatable(windows: dict[str, frozenset], beats) -> bool:
    """True when every pending ticket can still take a distinct remaining beat in its window."""
    remaining = set(beats)
    options = {ticket: sorted(window & remaining) for ticket, window in windows.items()}
    assigned: dict[int, str] = {}

    def place(ticket: str, seen: set) -> bool:
        for beat in options[ticket]:
            if beat in seen:
                continue
            seen.add(beat)
            holder = assigned.get(beat)
            if holder is None or place(holder, seen):
                assigned[beat] = ticket
                return True
        return False

    return all(place(ticket, set()) for ticket in options)


def _checked_windows(page: PhysicalPage, windows) -> dict[str, frozenset]:
    header = page.header()
    if not header.slots:
        raise ValueError('a windowed page requires at least one slot')
    if not isinstance(windows, dict):
        raise ValueError('windows must map each slot coordinate to a set of beats')
    if set(windows) != {slot.ref.coordinate for slot in page.slots}:
        raise ValueError('windows must name each page slot exactly once')
    checked = {}
    for coordinate, beats in windows.items():
        if isinstance(beats, (str, bytes)):
            raise ValueError('window beats must be positive SQLite integers')
        try:
            window = frozenset(beats)
        except TypeError as exc:
            raise ValueError('window beats must be positive SQLite integers') from exc
        if not window:
            raise ValueError('a window requires at least one beat')
        for beat in window:
            validate_position(beat)
        checked[coordinate] = window
    return checked


class RecorderFeed:
    def __init__(self, journal: RecorderJournal, status: RecorderStatus, lines: LineRegistry):
        self.journal = journal
        self.status = status
        self.lines = lines
        self.paused_slots: set[SlotRef] = set()
        self.slot_replies: dict[SlotRef, RawSlot] = {}

    def feed(self, page: PhysicalPage) -> PageHeader:
        return self._feed(page, None)

    def feed_windowed(self, page: PhysicalPage, windows: dict) -> PageHeader:
        return self._feed(page, _checked_windows(page, windows))

    def _feed(self, page: PhysicalPage, windows) -> PageHeader:
        self.status.device.require_online()
        db = self.journal.db
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                line = self.lines.get_line(page.line)
                if line.terminal_page is not None:
                    raise ValueError(f'line is stopped: {page.line}')
                header = page.header()
                if header.slots and header.slots[0].position < line.first_native_position:
                    raise ValueError('device page overlaps the reserved old tail')
                saved = self.journal.save_page(page)
                if windows is not None:
                    self.journal.save_windows(page.page_id, windows)
                return saved
        except sqlite3.IntegrityError as exc:
            raise ValueError('duplicate page identity or native position') from exc

    def _retained_page(self, page_id: str) -> PhysicalPage:
        self.status.device.require_online()
        if self.journal.is_windowed(page_id):
            raise PageUnavailable(page_id)
        page = self.journal.load_page(page_id)
        if page is None:
            raise PageUnavailable(page_id)
        return page

    def _reply(self, slot: RawSlot) -> RawSlot:
        if slot.ref in self.paused_slots:
            raise SlotPaused(f'slot transfer is paused: {slot.ref}')
        return self.slot_replies.get(slot.ref, slot)

    def read_page(self, page_id: str) -> PhysicalPage:
        page = self._retained_page(page_id)
        return PhysicalPage(page.page_id, page.line, tuple(self._reply(slot) for slot in page.slots))

    def read_slot(self, ref: SlotRef) -> RawSlot:
        for slot in self._retained_page(ref.page_id).slots:
            if slot.ref == ref:
                return self._reply(slot)
        raise PageUnavailable(ref)

    def _pending_windows(self, page_id: str, windows: dict) -> dict:
        done = {ticket for ticket in self.journal.page_reads(page_id).values() if ticket is not None}
        return {ticket: windows[coordinate]
                for ticket, coordinate in self.journal.page_bookings(page_id)
                if ticket not in done}

    def book(self, ticket: str, ref: SlotRef) -> str:
        self.status.device.require_online()
        if not isinstance(ticket, str) or not ticket.strip():
            raise ValueError('booking ticket must be a non-empty string')
        if not isinstance(ref, SlotRef):
            raise ValueError('booking requires a physical slot reference')
        db = self.journal.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.journal.get_booking(ticket)
            if existing is not None:
                if existing == (ref.page_id, ref.coordinate):
                    return 'accepted'
                raise ValueError(f'ticket is already booked for another slot: {ticket}')
            state = self.journal.window_state(ref.page_id)
            if state is None:
                raise ValueError(f'not a windowed page: {ref.page_id}')
            max_beat, next_beat = state
            windows = self.journal.window_beats(ref.page_id)
            if ref.coordinate not in windows:
                raise ValueError(f'unknown window slot: {ref}')
            if self.journal.slot_booking(ref.page_id, ref.coordinate) is not None:
                raise ValueError(f'slot already has a booking: {ref}')
            pending = self._pending_windows(ref.page_id, windows)
            pending[ticket] = windows[ref.coordinate]
            if not _allocatable(pending, range(next_beat, max_beat + 1)):
                return 'unavailable'
            self.journal.save_booking(ticket, ref.page_id, ref.coordinate)
            return 'accepted'

    def tick(self, page_id: str, beat: int) -> str | None:
        self.status.device.require_online()
        db = self.journal.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            state = self.journal.window_state(page_id)
            if state is None:
                raise ValueError(f'not a windowed page: {page_id}')
            max_beat, next_beat = state
            if type(beat) is not int or beat < 1:
                raise ValueError(f'invalid beat: {beat!r}')
            if beat > max_beat:
                raise ValueError(f'beat is beyond the page window: {beat}')
            reads = self.journal.page_reads(page_id)
            if beat in reads:
                return reads[beat]
            if beat != next_beat:
                raise ValueError(f'beat is not the next pending beat: {beat}')
            windows = self.journal.window_beats(page_id)
            pending = self._pending_windows(page_id, windows)
            remaining = range(beat + 1, max_beat + 1)
            chosen = None
            for candidate in sorted(pending):
                if beat not in pending[candidate]:
                    continue
                rest = {other: window for other, window in pending.items() if other != candidate}
                if _allocatable(rest, remaining):
                    chosen = candidate
                    break
            self.journal.save_read(page_id, beat, chosen, beat + 1)
            return chosen

    def read_terminal(self, ticket: str) -> WindowRead | None:
        self.status.device.require_online()
        found = self.journal.ticket_read(ticket)
        if found is None:
            return None
        page_id, beat = found
        booking = self.journal.get_booking(ticket)
        page = self.journal.load_page(page_id)
        if booking is None or page is None:
            return None
        for slot in page.slots:
            if slot.ref.coordinate == booking[1]:
                return WindowRead(ticket, slot, beat)
        return None
