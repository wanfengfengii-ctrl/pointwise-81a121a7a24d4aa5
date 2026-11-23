from capture.depot import CustodyDepot, RetirementState
from capture.retirement import RetirementService


class RetirementRecovery:
    def __init__(self, depot: CustodyDepot, retirement: RetirementService):
        self.depot = depot
        self.retirement = retirement

    def resume(self) -> dict[str, RetirementState]:
        """Resume only journalled requests, each by its original physical page ID."""
        resumed = {}
        for page_id in self.depot.pending_retirements():
            resumed[page_id] = self.retirement.retire(page_id)
        return resumed
