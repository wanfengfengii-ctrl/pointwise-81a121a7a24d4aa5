from recorder.journal import RecorderJournal
from recorder.status import DeviceAvailability, PageUnavailable, RetirementOutcome


class RetirementReplyLost(RuntimeError):
    pass


class RecorderReclaimer:
    def __init__(self, journal: RecorderJournal, device: DeviceAvailability):
        self.journal = journal
        self.device = device
        self.lose_reply: set[str] = set()
        self.defer_next: set[str] = set()

    def reclaim(self, page_id: str) -> RetirementOutcome:
        self.device.require_online()
        if page_id in self.defer_next:
            self.defer_next.remove(page_id)
            raise RetirementReplyLost(page_id)
        db = self.journal.db
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                changed = self.journal.retire_page(page_id)
        except KeyError as exc:
            raise PageUnavailable(page_id) from exc
        if changed and page_id in self.lose_reply:
            self.lose_reply.remove(page_id)
            raise RetirementReplyLost(page_id)
        return RetirementOutcome.RETIRED
