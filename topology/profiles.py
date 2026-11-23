from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class ConversionProfile:
    numerator: int
    denominator: int

    @property
    def multiplier(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


def build_profile(numerator: int, denominator: int) -> ConversionProfile:
    if type(numerator) is not int or type(denominator) is not int:
        raise ValueError('conversion ratio requires integers')
    if numerator <= 0 or denominator <= 0:
        raise ValueError('conversion ratio requires positive terms')
    ratio = Fraction(numerator, denominator)
    return ConversionProfile(ratio.numerator, ratio.denominator)
