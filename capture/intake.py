import sqlite3
from typing import Iterable

from capture.depot import CustodyDepot
from capture.evidence import EvidenceMismatch, VerifiedSlot, verify_slot
from recorder.feed import RecorderFeed
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
