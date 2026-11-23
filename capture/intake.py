import sqlite3
from typing import Iterable

from capture.depot import CustodyDepot
from capture.evidence import EvidenceMismatch, VerifiedSlot, verify_slot
from recorder.feed import RecorderFeed, WindowRead
from recorder.pages import PageHeader
from recorder.status import DeviceUnavailable, PageUnavailable


class CaptureCommitFailed(sqlite3.OperationalError):
    pass


class CaptureIntake:
    def __init__(self, feed: RecorderFeed, depot: CustodyDepot):
        self.feed = feed
        self.depot = depot
        self.fail_next: set[str] = set()

    def _directory(self, page_id: str, saved: PageHeader | None) -> PageHeader:
        try:
            header = self.feed.status.page_header(page_id)
        except DeviceUnavailable:
            if saved is None:
                raise
            return saved
        if saved is not None and header != saved:
            raise EvidenceMismatch(f'device directory changed: {page_id}')
        return header

    def accept(self, page_id: str, coordinates: Iterable[str] | None = None) -> tuple[VerifiedSlot, ...]:
        db = self.depot.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            if self.feed.journal.is_windowed(page_id):
                raise PageUnavailable(page_id)
            saved_header = self.depot.get_header(page_id)
            header = self._directory(page_id, saved_header)
            by_coordinate = {proof.ref.coordinate: proof for proof in header.slots}
            if coordinates is None:
                proofs = header.slots
            else:
                requested = tuple(coordinates)
                if not requested or len(set(requested)) != len(requested):
                    raise ValueError('explicit slot request requires distinct coordinates')
                try:
                    proofs = tuple(by_coordinate[coordinate] for coordinate in requested)
                except KeyError as exc:
                    raise PageUnavailable((page_id, exc.args[0])) from exc
            originals = []
            changed = saved_header is None
            for proof in proofs:
                existing = self.depot.get_slot(proof.ref)
                try:
                    raw = self.feed.read_slot(proof.ref)
                except (DeviceUnavailable, PageUnavailable):
                    if existing is None:
                        raise
                    verified = existing
                else:
                    verified = verify_slot(proof.ref, raw, proof)
                    if existing is not None and existing != verified:
                        raise EvidenceMismatch(f'conflicting original at {proof.ref}')
                originals.append(verified)
                changed = changed or existing is None
            self.depot.save_verified(header, originals)
            if changed and page_id in self.fail_next:
                self.fail_next.remove(page_id)
                raise CaptureCommitFailed(page_id)
        return tuple(originals)

    def collect(self, ticket: str, terminal: WindowRead | None = None):
        """核收一份预约实读：原件与预约实收出处一起提交，重投沿用原实收。"""
        db = self.depot.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            committed = self.depot.get_window_read(ticket)
            if committed is not None:
                if terminal is not None and terminal != committed:
                    raise EvidenceMismatch(f'terminal does not match the collected read: {ticket}')
                return committed
            if terminal is not None and getattr(terminal, 'ticket', None) != ticket:
                raise EvidenceMismatch(f'terminal ticket does not match request: {ticket}')
            actual = self.feed.read_terminal(ticket)
            if actual is None:
                return 'pending'
            candidate = actual if terminal is None else terminal
            if candidate != actual:
                raise EvidenceMismatch(f'terminal does not match the device read: {ticket}')
            ref = candidate.slot.ref
            header = self._directory(ref.page_id, self.depot.get_header(ref.page_id))
            proofs = {proof.ref: proof for proof in header.slots}
            if ref not in proofs:
                raise EvidenceMismatch(f'terminal slot is not in the device directory: {ref}')
            verified = verify_slot(ref, candidate.slot, proofs[ref])
            self.depot.save_verified(header, (verified,))
            self.depot.save_window_read(candidate)
            if ref.page_id in self.fail_next:
                self.fail_next.remove(ref.page_id)
                raise CaptureCommitFailed(ref.page_id)
            return candidate

    def lookup_read(self, ticket: str) -> WindowRead | None:
        return self.depot.get_window_read(ticket)
