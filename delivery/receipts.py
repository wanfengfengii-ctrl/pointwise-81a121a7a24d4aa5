from dataclasses import dataclass
from hashlib import sha256
import json

from delivery.continuity import DeliverySegment


@dataclass(frozen=True)
class PublicationKey:
    line: str
    through: int
    page_ids: tuple[str, ...]

    def encode(self) -> str:
        return json.dumps((self.line, self.through, self.page_ids), separators=(',', ':'))


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    result_id: str
    line: str
    start: int
    through: int
    unit: str = 'um/s'


def publication_key(line: str, segment: DeliverySegment) -> PublicationKey:
    return PublicationKey(line, segment.through, segment.page_ids)


def build_receipt(key: PublicationKey, start: int) -> Receipt:
    token = sha256(key.encode().encode('ascii')).hexdigest()
    return Receipt(f'receipt-{token}', f'result-{token}', key.line, start, key.through)
