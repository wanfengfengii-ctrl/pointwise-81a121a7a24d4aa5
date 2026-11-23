from dataclasses import dataclass
from hashlib import sha256

from recorder.pages import RawSlot, SlotEvidence, SlotRef, decode_integer


class EvidenceMismatch(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedSlot:
    ref: SlotRef
    line: str
    position: int
    label: str
    payload: bytes
    digest: str

    def evidence(self) -> SlotEvidence:
        return SlotEvidence(self.ref, self.line, self.position, self.label, self.digest)


def verify_slot(requested: SlotRef, raw: RawSlot, proof: SlotEvidence) -> VerifiedSlot:
    if raw.ref != requested or proof.ref != requested:
        raise EvidenceMismatch(f'slot identity does not match request: {requested}')
    if (raw.position, raw.label) != (proof.position, proof.label):
        raise EvidenceMismatch(f'slot metadata does not match directory: {requested}')
    if not isinstance(raw.payload, bytes) or sha256(raw.payload).hexdigest() != proof.digest:
        raise EvidenceMismatch(f'original bytes do not match directory: {requested}')
    try:
        decode_integer(raw.payload)
    except ValueError as exc:
        raise EvidenceMismatch(f'invalid vibration integer at {requested}') from exc
    return VerifiedSlot(requested, proof.line, proof.position, proof.label, raw.payload, proof.digest)
