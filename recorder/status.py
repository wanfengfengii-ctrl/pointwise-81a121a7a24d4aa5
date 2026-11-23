from enum import Enum

from recorder.journal import RecorderJournal
from recorder.pages import PageHeader


class DeviceUnavailable(RuntimeError):
    pass


class PageUnavailable(LookupError):
    pass


class DeviceAvailability:
    def __init__(self) -> None:
        self.online = True

    def stop(self) -> None:
        self.online = False

    def require_online(self) -> None:
        if not self.online:
            raise DeviceUnavailable('recorder is offline')


class RetirementOutcome(Enum):
    RETAINED = 'retained'
    RETIRED = 'retired'


class RecorderStatus:
    def __init__(self, journal: RecorderJournal, device: DeviceAvailability):
        self.journal = journal
        self.device = device

    def page_header(self, page_id: str) -> PageHeader:
        self.device.require_online()
        header = self.journal.get_header(page_id)
        if header is None:
            raise PageUnavailable(page_id)
        return header

    def query_retirement(self, page_id: str) -> RetirementOutcome:
        self.device.require_online()
        try:
            is_retired = self.journal.get_retirement(page_id)
        except KeyError as exc:
            raise PageUnavailable(page_id) from exc
        return RetirementOutcome.RETIRED if is_retired else RetirementOutcome.RETAINED
