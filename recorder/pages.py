from dataclasses import dataclass
from hashlib import sha256
import re


MAX_POSITION = (1 << 63) - 1
_DECIMAL = re.compile(rb'[+-]?[0-9]+')


def validate_position(position: int) -> None:
    if type(position) is not int or not 1 <= position <= MAX_POSITION:
        raise ValueError('position must be a positive SQLite integer')


def decode_integer(payload: bytes) -> int:
    if not isinstance(payload, bytes) or not _DECIMAL.fullmatch(payload):
        raise ValueError('raw slot is not a signed ASCII decimal integer')
    return int(payload)


@dataclass(frozen=True, order=True)
class SlotRef:
    page_id: str
    coordinate: str


@dataclass(frozen=True)
class RawSlot:
    ref: SlotRef
    position: int
    label: str
    payload: bytes


@dataclass(frozen=True)
class SlotEvidence:
    ref: SlotRef
    line: str
    position: int
    label: str
    digest: str


@dataclass(frozen=True)
class PageHeader:
    page_id: str
    line: str
    slots: tuple[SlotEvidence, ...]


@dataclass(frozen=True)
class PhysicalPage:
    page_id: str
    line: str
    slots: tuple[RawSlot, ...]

    def header(self) -> PageHeader:
        if not isinstance(self.page_id, str) or not self.page_id.strip():
            raise ValueError('page ID is required')
        seen = set()
        previous = 0
        entries = []
        for slot in self.slots:
            validate_position(slot.position)
            coordinate = slot.ref.coordinate
            if (slot.ref.page_id != self.page_id or not isinstance(coordinate, str)
                    or not coordinate.strip() or coordinate in seen):
                raise ValueError('page contains an invalid or repeated physical slot')
            if slot.position <= previous:
                raise ValueError('page positions must be strictly increasing')
            if not isinstance(slot.label, str) or not slot.label.strip():
                raise ValueError('slot label is required')
            if not isinstance(slot.payload, bytes):
                raise ValueError('recorder payload must be bytes')
            entries.append(SlotEvidence(slot.ref, self.line, slot.position, slot.label,
                                        sha256(slot.payload).hexdigest()))
            previous = slot.position
            seen.add(coordinate)
        return PageHeader(self.page_id, self.line, tuple(entries))
