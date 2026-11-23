import sqlite3

from recorder.journal import RecorderJournal
from recorder.pages import PageHeader, PhysicalPage, RawSlot, SlotRef
from recorder.status import DeviceUnavailable, PageUnavailable, RecorderStatus
from topology.lines import LineRegistry


class SlotPaused(DeviceUnavailable):
    pass


class RecorderFeed:
    def __init__(self, journal: RecorderJournal, status: RecorderStatus, lines: LineRegistry):
        self.journal = journal
        self.status = status
        self.lines = lines
        self.paused_slots: set[SlotRef] = set()
        self.slot_replies: dict[SlotRef, RawSlot] = {}

    def feed(self, page: PhysicalPage) -> PageHeader:
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
                return self.journal.save_page(page)
        except sqlite3.IntegrityError as exc:
            raise ValueError('duplicate page identity or native position') from exc

    def _retained_page(self, page_id: str) -> PhysicalPage:
        self.status.device.require_online()
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
