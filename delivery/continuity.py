from dataclasses import dataclass

from capture.depot import CustodyDepot
from capture.evidence import VerifiedSlot
from recorder.pages import validate_position
from topology.slots import assignments_for_line
from topology.splits import SplitManifest


class ContinuityGap(ValueError):
    pass


@dataclass(frozen=True)
class DeliverySegment:
    through: int
    page_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_position(self.through)
        if isinstance(self.page_ids, str):
            raise ValueError('page IDs must be a collection of page identifiers')
        pages = tuple(self.page_ids)
        if any(not isinstance(page, str) or not page.strip() for page in pages):
            raise ValueError('segment requires valid page IDs')
        if len(set(pages)) != len(pages):
            raise ValueError('segment contains repeated page IDs')
        object.__setattr__(self, 'page_ids', tuple(sorted(pages)))


def plan_segment(line: str, frontier: int | None, segment: DeliverySegment,
                 manifest: SplitManifest | None, depot: CustodyDepot) -> tuple[tuple[int, VerifiedSlot], ...]:
    tail = assignments_for_line(manifest.assignments, line) if manifest else ()
    start = frontier + 1 if frontier is not None else (tail[0].position if tail else 1)
    if segment.through < start:
        raise ContinuityGap(f'{line} requires position {start} or later')
    positions = {}
    old_positions = [entry for entry in tail if start <= entry.position <= segment.through]
    for assignment in old_positions:
        slot = depot.get_slot(assignment.ref)
        if slot is None or slot.line != manifest.source:
            raise ContinuityGap(f'{line} is missing old tail at position {assignment.position}')
        positions[assignment.position] = slot

    nonempty_page = False
    for page_id in segment.page_ids:
        header = depot.get_header(page_id)
        if header is None or header.line != line:
            raise ContinuityGap(f'{line} does not have custody of native page {page_id}')
        if not header.slots:
            continue
        nonempty_page = True
        requested = [proof for proof in header.slots if start <= proof.position <= segment.through]
        if not requested:
            raise ContinuityGap(f'page {page_id} has no slots in the requested segment')
        available = {slot.ref: slot for slot in depot.slots_for_page(page_id)}
        for proof in requested:
            slot = available.get(proof.ref)
            if slot is None or slot.evidence() != proof:
                raise ContinuityGap(f'{line} has no verified original at position {proof.position}')
            if proof.position in positions:
                raise ContinuityGap(f'{line} has competing originals at position {proof.position}')
            positions[proof.position] = slot

    if segment.page_ids and not nonempty_page and not old_positions:
        return ()
    ordered = tuple(sorted(positions.items()))
    for expected, (position, _) in enumerate(ordered, start):
        if position != expected:
            raise ContinuityGap(f'{line} is missing position {expected}')
    if len(ordered) != segment.through - start + 1:
        raise ContinuityGap(f'{line} is missing position {start + len(ordered)}')
    return ordered
