from capture.depot import CustodyDepot, RetirementState
from recorder.pages import PageHeader
from recorder.reclaim import RecorderReclaimer, RetirementReplyLost
from recorder.status import DeviceUnavailable, RecorderStatus, RetirementOutcome


class RetirementConflict(RuntimeError):
    pass


class RetirementService:
    def __init__(self, depot: CustodyDepot, status: RecorderStatus, reclaimer: RecorderReclaimer):
        self.depot = depot
        self.status = status
        self.reclaimer = reclaimer

    def _fully_held(self, header: PageHeader) -> bool:
        if self.depot.get_header(header.page_id) != header:
            return False
        originals = self.depot.slots_for_page(header.page_id)
        return tuple(slot.evidence() for slot in originals) == header.slots

    def retire(self, page_id: str) -> RetirementState:
        db = self.depot.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            state = self.depot.get_retirement(page_id)
            if state is RetirementState.CONFIRMED:
                return state
            try:
                outcome = self.status.query_retirement(page_id)
            except DeviceUnavailable:
                if state is RetirementState.PENDING:
                    return state
                raise
            if outcome is RetirementOutcome.RETIRED:
                if state is not RetirementState.PENDING:
                    raise RetirementConflict(f'no matching custody request for retired page {page_id}')
                self.depot.save_retirement(page_id, RetirementState.CONFIRMED)
                return RetirementState.CONFIRMED
            if not self._fully_held(self.status.page_header(page_id)):
                return state or RetirementState.INCOMPLETE
            self.depot.save_retirement(page_id, RetirementState.PENDING)
        try:
            outcome = self.reclaimer.reclaim(page_id)
        except (DeviceUnavailable, RetirementReplyLost):
            return RetirementState.PENDING
        if outcome is RetirementOutcome.RETIRED:
            with db:
                self.depot.save_retirement(page_id, RetirementState.CONFIRMED)
            return RetirementState.CONFIRMED
        return RetirementState.PENDING
