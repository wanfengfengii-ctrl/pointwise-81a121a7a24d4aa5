from dataclasses import dataclass
from fractions import Fraction

from delivery.results import DeliveryResult
from recorder.pages import SlotRef


@dataclass(frozen=True)
class ProvenanceEntry:
    position: int
    label: str
    source: SlotRef
    value: Fraction


def list_provenance(result: DeliveryResult) -> tuple[ProvenanceEntry, ...]:
    return tuple(ProvenanceEntry(row.position, row.label, row.source, row.value)
                 for row in result.readings)
