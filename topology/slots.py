from dataclasses import dataclass
from typing import Iterable

from recorder.pages import MAX_POSITION, PageHeader, SlotRef, validate_position


@dataclass(frozen=True)
class SlotAssignment:
    ref: SlotRef
    successor: str
    position: int


def assignments_for_line(assignments: Iterable[SlotAssignment], line: str) -> tuple[SlotAssignment, ...]:
    return tuple(sorted((item for item in assignments if item.successor == line),
                        key=lambda item: item.position))


def validate_assignments(header: PageHeader, successors: tuple[str, str],
                         assignments: Iterable[SlotAssignment]) -> tuple[SlotAssignment, ...]:
    assignments = tuple(assignments)
    if len(successors) != 2 or len(set(successors)) != 2:
        raise ValueError('a split requires two distinct successors')
    available = {entry.ref for entry in header.slots}
    assigned = set()
    positions = set()
    for item in assignments:
        validate_position(item.position)
        if item.ref not in available or item.ref in assigned:
            raise ValueError(f'nonterminal or repeated source slot: {item.ref}')
        if item.successor not in successors:
            raise ValueError(f'not a successor: {item.successor}')
        route_position = (item.successor, item.position)
        if route_position in positions:
            raise ValueError(f'repeated continuation position: {route_position}')
        assigned.add(item.ref)
        positions.add(route_position)
    if assigned != available:
        raise ValueError('manifest must cover the complete terminal page')
    ordered = []
    for line in successors:
        tail = assignments_for_line(assignments, line)
        if tail:
            first, last = tail[0].position, tail[-1].position
            if last - first + 1 != len(tail) or last == MAX_POSITION:
                raise ValueError(f'old tail has no valid continuation on {line}')
        ordered.extend(tail)
    return tuple(ordered)
