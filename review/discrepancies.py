from dataclasses import dataclass
from fractions import Fraction

from recorder.pages import SlotRef
from review.provenance import ProvenanceEntry


@dataclass(frozen=True)
class ReviewExpectation:
    position: int
    label: str
    source: SlotRef
    value: Fraction


@dataclass(frozen=True)
class Discrepancy:
    position: int | None
    field: str
    expected: str | None
    actual: str | None


def _display(value: object) -> str:
    if isinstance(value, SlotRef):
        return repr((value.page_id, value.coordinate))
    return str(value)


def compare_checklist(checklist: tuple[ReviewExpectation, ...],
                      actual: tuple[ProvenanceEntry, ...]) -> tuple[Discrepancy, ...]:
    by_position = {row.position: row for row in actual}
    expected_positions = {row.position for row in checklist}
    common_expected = [row.position for row in checklist if row.position in by_position]
    common_actual = [row.position for row in actual if row.position in expected_positions]
    differences = []
    if common_expected != common_actual:
        differences.append(Discrepancy(None, 'order', str(common_expected), str(common_actual)))
    for wanted in checklist:
        observed = by_position.get(wanted.position)
        if observed is None:
            differences.append(Discrepancy(wanted.position, 'missing', wanted.label, None))
            continue
        for field in ('label', 'source', 'value'):
            expected_value = getattr(wanted, field)
            actual_value = getattr(observed, field)
            if expected_value != actual_value:
                differences.append(Discrepancy(wanted.position, field, _display(expected_value),
                                               _display(actual_value)))
    for observed in actual:
        if observed.position not in expected_positions:
            differences.append(Discrepancy(observed.position, 'unexpected', None, observed.label))
    return tuple(differences)
