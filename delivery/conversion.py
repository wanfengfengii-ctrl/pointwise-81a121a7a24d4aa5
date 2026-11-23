from dataclasses import dataclass
from fractions import Fraction

from capture.evidence import VerifiedSlot
from recorder.pages import SlotRef, decode_integer
from topology.profiles import ConversionProfile


@dataclass(frozen=True)
class ConvertedReading:
    position: int
    label: str
    source: SlotRef
    value: Fraction


def convert_reading(position: int, original: VerifiedSlot, profile: ConversionProfile) -> ConvertedReading:
    value = decode_integer(original.payload) * profile.multiplier
    return ConvertedReading(position, original.label, original.ref, value)
