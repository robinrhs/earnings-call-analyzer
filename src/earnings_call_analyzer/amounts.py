"""Find the financial amounts in a piece of text: sign, number, unit and currency.

Both the quote verifier and the quarter-over-quarter diff need to read numbers the way
an analyst does, not the way a regex for digits does:

* **The sign is part of the number.** "-3%", "(3)%" (accounting brackets) and
  the same with a typographic minus sign (U+2212) all mean minus three percent.
* **The unit is part of the number.** "$1.62 million" and "$1.62 billion" differ by a
  factor of 1,000; "15%" and "15 bps" are different things.
* **Period labels are not amounts.** "Q4", "FY2026", "H2", "2Q" and a bare year such
  as "fiscal 2026" name a period. Counting them as amounts would put "2026" into a
  revenue range.

Nothing here knows about transcripts or reports; it only turns text into
:class:`Amount` objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A number with an optional currency sign ($, euro, pound) in front: "$1,210.5", "15".
_NUMBER = re.compile(r"(?P<currency>[$\u20ac\u00a3])?(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)")

# The unit right after a number (optionally after one space), longest spelling first.
_UNIT = re.compile(
    r"\s?(?P<unit>%|percentage points?\b|percent\b|per cent\b|pp\b|basis points?\b|bps\b"
    r"|thousand\b|million\b|billion\b|trillion\b|mn\b|bn\b|k\b|m\b|b\b|x\b)",
    re.IGNORECASE,
)
_UNIT_NAMES = {
    "%": "%",
    "percent": "%",
    "per cent": "%",
    "percentage point": "pp",
    "percentage points": "pp",
    "pp": "pp",
    "basis point": "bps",
    "basis points": "bps",
    "bps": "bps",
    "thousand": "thousand",
    "k": "thousand",
    "million": "million",
    "mn": "million",
    "m": "million",
    "billion": "billion",
    "bn": "billion",
    "b": "billion",
    "trillion": "trillion",
    "x": "x",
}
# Scale words, expressed in millions (the unit the QoQ diff compares money in).
SCALE_TO_MILLIONS = {"thousand": 1e-3, "million": 1.0, "billion": 1e3, "trillion": 1e6}

# Words that make the amounts in a statement negative: "an operating loss of $20 million",
# "a decline of 3%". Used where a sign is implied by wording instead of a minus sign.
NEGATIVE_WORDING = re.compile(
    r"\b(?:loss|losses|decline|declines|declined|decrease|decreases|decreased|down|negative"
    r"|contraction|minus)\b",
    re.IGNORECASE,
)

_MINUS_SIGNS = "-\u2212\u2013"  # hyphen-minus, typographic minus, en dash used as minus
# A minus sign only counts when it starts a word: "4%-6%" is a range, not minus six.
_BEFORE_SIGN = " \t\n([{\"'/"


@dataclass(frozen=True)
class Amount:
    value: float  # signed, as written: "-3%" -> -3.0, "$1.62 billion" -> 1.62
    unit: str  # "%", "pp", "bps", "x", "thousand", "million", "billion", "trillion" or ""
    currency: str  # "$", euro sign, pound sign or ""
    start: int  # position of the amount in the text (including sign and currency)
    end: int  # position just after the amount (including its unit)

    def in_millions(self) -> float | None:
        """The value in millions if the unit is a scale word, else None."""
        scale = SCALE_TO_MILLIONS.get(self.unit)
        # Rounded so that 1.66 billion compares equal to 1,660 million.
        return round(self.value * scale, 6) if scale is not None else None


def _is_period_label(text: str, start: int, end: int) -> bool:
    """True for the digits of 'Q4', 'FY2026', 'H2', '2Q26', '3rd' - glued to letters."""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return before.isalpha() or before in "._" or after.isalpha() or after == "_"


def _is_bare_year(number: str, currency: str, unit: str, negative: bool) -> bool:
    """'fiscal 2026', 'in 2025': a four-digit number from 1900 to 2100 with nothing else."""
    if currency or unit or negative or not re.fullmatch(r"\d{4}", number):
        return False
    return 1900 <= int(number) <= 2100


def find_amounts(text: str) -> list[Amount]:
    """All amounts in ``text``, left to right, with period labels and bare years removed.

    >>> [(a.value, a.unit, a.currency) for a in find_amounts("down -3% to $1.6bn in Q4")]
    [(-3.0, '%', ''), (1.6, 'billion', '$')]
    """
    amounts = []
    for match in _NUMBER.finditer(text):
        start, end = match.start(), match.end()
        currency, number = match.group("currency") or "", match.group("number")

        # Accounting brackets: "(3.2)" or "(3)%" is a negative number.
        negative = False
        if start > 0 and text[start - 1] == "(" and text[end : end + 1] == ")":
            negative = True
            start, end = start - 1, end + 1
        # A minus sign directly in front, at the start of a word: "-3%", "-$20".
        elif (
            start > 0
            and text[start - 1] in _MINUS_SIGNS
            and (start == 1 or text[start - 2] in _BEFORE_SIGN)
        ):
            negative = True
            start -= 1

        unit = ""
        if unit_match := _UNIT.match(text, end):
            unit = _UNIT_NAMES[unit_match.group("unit").casefold()]
            end = unit_match.end()

        number_start = match.start("number") if not currency else match.start()
        number_end = match.end("number")
        if not unit and _is_period_label(text, number_start, number_end):
            continue
        if _is_bare_year(number, currency, unit, negative):
            continue

        value = float(number.replace(",", ""))
        amounts.append(
            Amount(
                value=-value if negative else value,
                unit=unit,
                currency=currency,
                start=start,
                end=end,
            )
        )
    return amounts


def signed_numbers(text: str) -> list[float]:
    """Just the signed values: '$1,210.5 million, down -3%' -> [1210.5, -3.0]."""
    return [amount.value for amount in find_amounts(text)]


__all__ = ["NEGATIVE_WORDING", "SCALE_TO_MILLIONS", "Amount", "find_amounts", "signed_numbers"]
