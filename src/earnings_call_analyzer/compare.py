"""Quarter-over-quarter comparison of two verified briefs.

Analysts read a call against the previous one. The questions are always the same:

* **Guidance** - raised, lowered or maintained? Did a quarter we had guidance for come
  in above, within or below that range?
* **Risks** - which concerns are new, which persist, which disappeared?
* **Tone** - is management more or less confident than three months ago?

Only items that passed verification (verified or corrected) *and* carry no number
warning are compared. A real quote next to a guidance range the model typed wrongly
must not drive a "raised" call, so such items are left out and listed as warnings.

Everything here is plain rule-based logic on the two briefs: no second model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel

from .amounts import NEGATIVE_WORDING, RANGE_JOIN, Amount, find_amounts
from .schema import GuidanceItem, HeadlineFigure, Risk, ToneLabel
from .verify import Checked, VerifiedBrief

GuidanceChangeKind = Literal[
    "raised", "lowered", "maintained", "changed", "new", "dropped", "reported", "not_compared"
]
RiskChangeKind = Literal["new", "persisting", "dropped"]
# How the kind of number in a guidance range is compared and printed.
ValueKind = Literal["percent", "pp", "bps", "multiple", "millions", "currency", "plain"]

# Ordered from most negative to most positive, so a tone shift is a difference of ranks.
TONE_SCALE: list[ToneLabel] = ["defensive", "cautious", "balanced", "constructive", "confident"]

# A midpoint change smaller than this share of the larger midpoint counts as "maintained".
# Using the larger of the two (not the old midpoint) keeps the rule sensible at zero.
MAINTAINED_TOLERANCE = 0.005

# Two risk titles in the same category that share at least this share of their words
# are "the same risk"; below it, a pairing rests on the category alone and says so.
TITLE_MATCH = 0.3

_STOPWORDS = {"the", "of", "for", "full", "year", "guidance", "total", "and", "a"}


class GuidanceChange(BaseModel):
    metric: str
    period: str
    previous: str | None
    current: str | None
    change: GuidanceChangeKind
    stated_direction: str | None
    detail: str


class RiskChange(BaseModel):
    category: str
    change: RiskChangeKind
    previous_title: str | None
    current_title: str | None
    # For "persisting": "title" if the titles overlap, "category" if only the category
    # matches (a weaker link that the reader should check).
    matched_on: Literal["title", "category"] | None = None


class ToneShift(BaseModel):
    previous: ToneLabel
    current: ToneLabel
    steps: int | None  # None when the tone was not compared
    description: str


class FigurePair(BaseModel):
    metric: str
    previous: str | None
    current: str | None


class Comparison(BaseModel):
    company: str
    previous_period: str
    current_period: str
    guidance: list[GuidanceChange]
    risks: list[RiskChange]
    tone: ToneShift
    figures: list[FigurePair]
    warnings: list[str] = []


# --- Reading a guidance range -----------------------------------------------------------


@dataclass(frozen=True)
class GuidanceRange:
    """The numeric core of a guidance string: '$1.62 to $1.66 billion' -> 1,620 to 1,660."""

    low: float
    high: float
    kind: ValueKind
    currency: str
    others: tuple[float, ...]  # other amounts in the same text, outside the range

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2


def _comparable(a: GuidanceRange, b: GuidanceRange) -> bool:
    """Same kind of number, and no clash of currencies ("$1.6bn" vs "1,600 million" is fine)."""
    same_currency = a.currency == b.currency or not a.currency or not b.currency
    return a.kind == b.kind and same_currency


def _kind(unit: str, currency: str) -> ValueKind:
    if unit == "%":
        return "percent"
    if unit in ("pp", "bps"):
        return unit  # type: ignore[return-value]
    if unit == "x":
        return "multiple"
    if unit in ("thousand", "million", "billion", "trillion"):
        return "millions"
    return "currency" if currency else "plain"


def _scaled(amount: Amount, unit: str) -> float:
    """Money with a scale word is compared in millions; everything else as written."""
    shifted = Amount(amount.value, unit, amount.currency, amount.start, amount.end)
    in_millions = shifted.in_millions()
    return in_millions if in_millions is not None else amount.value


def parse_guidance(text: str, metric: str = "") -> GuidanceRange | None:
    """Find the guided range in a guidance string, or None if there is no usable number.

    * The range is the first amount with a unit or currency, plus the next amount if
      only "to", "-", "and" or "through" stands between them. "$1.62 to $1.66 billion":
      the first end takes the unit of the second, the usual shorthand.
    * Period labels and years ("Q4", "fiscal 2026") are never part of a range.
    * A lone number without unit or currency ("three new plants") is skipped.
    * If the metric or the words before the range describe a loss or a decline, the
      amounts are read as negative (unless a minus sign is already there).
    """
    amounts = find_amounts(text)
    for i, first in enumerate(amounts):
        second = amounts[i + 1] if i + 1 < len(amounts) else None
        if second is not None and not RANGE_JOIN.fullmatch(text[first.end : second.start]):
            second = None
        unit = first.unit or (second.unit if second else "")
        currency = first.currency or (second.currency if second else "")
        if second is not None and _kind(second.unit or unit, currency) != _kind(unit, currency):
            second = None  # "$1.6 billion, 8%" is two different figures, not a range
            unit, currency = first.unit, first.currency
        if not unit and not currency and second is None:
            continue

        ends = [first] if second is None else [first, second]
        values = [_scaled(end, end.unit or unit) for end in ends]
        # "an operating loss of $20 million to $10 million" is a range from -20 to -10,
        # so a wider loss next quarter reads as lowered guidance, as it should.
        loss_wording = NEGATIVE_WORDING.search(metric) or NEGATIVE_WORDING.search(
            text[: first.start]
        )
        if loss_wording and all(v >= 0 for v in values):
            values = [-v for v in values]
        others = tuple(a.value for a in amounts if a not in ends)
        return GuidanceRange(
            low=min(values),
            high=max(values),
            kind=_kind(unit, currency),
            currency=currency,
            others=others,
        )
    return None


def midpoint(text: str, metric: str = "") -> tuple[float, ValueKind] | None:
    """Midpoint of the guided range (or the single number) and its kind."""
    guided = parse_guidance(text, metric)
    return (guided.midpoint, guided.kind) if guided else None


def metric_key(metric: str) -> str:
    words = re.findall(r"[a-z]+", metric.casefold())
    return " ".join(sorted(w for w in words if w not in _STOPWORDS))


def period_key(period: str) -> str:
    """Normalise a period so the same period matches across two calls.

    'Q4 FY2026', 'fourth quarter fiscal 2026' -> 'Q4 2026'; 'H2 FY2026', 'second half
    of 2026' -> 'H2 2026'; 'calendar 2026', 'CY2026' -> 'CY 2026'; 'FY2026' -> 'FY 2026'.
    Half years and calendar years get their own keys so they never overwrite full-year
    guidance for the same metric.
    """
    text = period.casefold()
    year_match = re.search(r"(?:fy|cy)?\s?(20\d{2})|(?:fy|cy)\s?(\d{2})\b", text)
    year = ""
    if year_match:
        year = year_match.group(1) or f"20{year_match.group(2)}"
    ordinals = {"first": "1", "second": "2", "third": "3", "fourth": "4"}

    if q := re.search(r"\bq([1-4])\b|\b([1-4])q", text):
        return f"Q{q.group(1) or q.group(2)} {year}".strip()
    for word, number in ordinals.items():
        if f"{word} quarter" in text:
            return f"Q{number} {year}".strip()
    if h := re.search(r"\bh([12])\b|\b([12])h", text):
        return f"H{h.group(1) or h.group(2)} {year}".strip()
    for word in ("first", "second"):
        if f"{word} half" in text:
            return f"H{ordinals[word]} {year}".strip()
    if "calendar" in text or re.search(r"\bcy\s?\d", text):
        return f"CY {year}".strip()
    return f"FY {year}".strip()


# --- Comparing two values ---------------------------------------------------------------


def _money(value: float, currency: str) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{currency}{abs(value):,.2f}"


def _describe_move(p: float, c: float, kind: ValueKind, currency: str) -> str:
    delta = c - p
    # A relative change only makes sense against a positive base (not for a loss or 0).
    relative = f", {delta / p:+.1%}" if p > 0 else ""
    if kind == "percent":
        # Margins and growth rates move in percentage points (pp), not percent of percent.
        return f"Midpoint {p:.4g}% -> {c:.4g}% ({delta:+.4g} pp)."
    if kind == "pp":
        return f"Midpoint {p:.4g} pp -> {c:.4g} pp ({delta:+.4g} pp)."
    if kind == "bps":
        return f"Midpoint {p:,.4g} bps -> {c:,.4g} bps ({delta:+,.4g} bps)."
    if kind == "multiple":
        return f"Midpoint {p:.4g}x -> {c:.4g}x ({delta:+.4g}x)."
    if kind == "millions":
        return f"Midpoint {p:,.4g} -> {c:,.4g} million ({delta:+,.4g} million{relative})."
    if kind == "currency":
        # Per-share amounts such as EPS: no "million".
        sign = "+" if delta >= 0 else "-"
        return (
            f"Midpoint {_money(p, currency)} -> {_money(c, currency)} "
            f"({sign}{_money(abs(delta), currency)}{relative})."
        )
    return f"Midpoint {p:,.4g} -> {c:,.4g} ({delta:+,.4g}{relative})."


def _compare_values(
    previous: str, current: str, metric: str = ""
) -> tuple[GuidanceChangeKind, str]:
    prev, curr = parse_guidance(previous, metric), parse_guidance(current, metric)
    if prev is None or curr is None or not _comparable(prev, curr):
        same = re.sub(r"\s+", " ", previous.casefold()) == re.sub(r"\s+", " ", current.casefold())
        return (
            ("maintained", "Same wording.") if same else ("changed", "Not comparable as numbers.")
        )
    p, c = prev.midpoint, curr.midpoint
    if abs(c - p) <= MAINTAINED_TOLERANCE * max(abs(p), abs(c)):
        kind: GuidanceChangeKind = "maintained"
        detail = "Midpoint unchanged."
    else:
        kind = "raised" if c > p else "lowered"
        detail = _describe_move(p, c, curr.kind, curr.currency)
    if prev.others != curr.others:
        detail += " Other figures in the wording differ too; read both statements."
    return kind, detail


# --- Which items may be compared --------------------------------------------------------

T = TypeVar("T")


def _usable(
    items: list[Checked[T]],
    what: str,
    period: str,
    warnings: list[str],
    withheld: list[T] | None = None,
) -> list[T]:
    """Items that passed verification and whose numbers appear in the source paragraph.

    Anything left out is named in ``warnings`` (and added to ``withheld`` if given) so
    the reader knows the diff is partial.
    """
    usable = []
    for checked in items:
        if checked.status == "unverified":
            warnings.append(
                f"{period}: {what} '{_label(checked.item)}' not compared: quote unverified."
            )
        elif checked.warnings:
            reasons = " ".join(checked.warnings)
            warnings.append(f"{period}: {what} '{_label(checked.item)}' not compared: {reasons}")
        else:
            usable.append(checked.item)
            continue
        if withheld is not None:
            withheld.append(checked.item)
    return usable


def _label(item: object) -> str:
    if isinstance(item, GuidanceItem):
        return f"{item.metric} ({item.period})"
    if isinstance(item, HeadlineFigure):
        return item.metric
    if isinstance(item, Risk):
        return item.title
    return str(item)


# --- Guidance ---------------------------------------------------------------------------


def _actual_vs_range(guided: str, actual: str, metric: str = "") -> str:
    """Where a reported number landed relative to the range guided last quarter."""
    guide, result = parse_guidance(guided, metric), parse_guidance(actual, metric)
    if (
        guide is None
        or result is None
        or (guide.kind, guide.currency)
        != (
            result.kind,
            result.currency,
        )
    ):
        return f"Reported {actual} vs guided {guided}."
    low, high, value = guide.low, guide.high, result.low
    if value > high:
        position = "above the guided range"
    elif value < low:
        position = "below the guided range"
    elif value >= (low + high) / 2:
        position = "within the guided range (upper half)"
    else:
        position = "within the guided range (lower half)"
    return f"Reported {actual}, {position} of {guided}."


def _index_guidance(
    items: list[GuidanceItem], period: str, warnings: list[str]
) -> dict[tuple[str, str], GuidanceItem]:
    """Guidance by (metric, period). A duplicate key is reported, never overwritten."""
    by_key: dict[tuple[str, str], GuidanceItem] = {}
    for item in items:
        key = (metric_key(item.metric), period_key(item.period))
        if key in by_key:
            first = by_key[key]
            warnings.append(
                f"{period}: two guidance items for {item.metric} ({item.period}): "
                f"'{first.current}' and '{item.current}'. Only the first is compared."
            )
            continue
        by_key[key] = item
    return by_key


def compare_guidance(
    previous: list[GuidanceItem],
    current: list[GuidanceItem],
    current_period: str,
    current_figures: list[HeadlineFigure],
    previous_period: str = "previous call",
    withheld: list[GuidanceItem] | None = None,
) -> tuple[list[GuidanceChange], list[str]]:
    """Guidance changes, plus warnings about items that could not be compared.

    ``withheld`` are guidance items from either call that failed the checks. Their
    counterpart is reported as "not_compared" instead of "new" or "dropped", because
    the guidance was given; it just cannot be trusted as extracted.
    """
    warnings: list[str] = []
    withheld_keys = {(metric_key(g.metric), period_key(g.period)) for g in withheld or []}
    prev_by_key = _index_guidance(previous, previous_period, warnings)
    curr_by_key = _index_guidance(current, current_period, warnings)
    changes: list[GuidanceChange] = []

    for key, cur in curr_by_key.items():
        prev = prev_by_key.get(key)
        if prev is not None:
            kind, detail = _compare_values(prev.current, cur.current, cur.metric)
            previous_text = prev.current
        elif cur.prior:
            # Not in last quarter's brief, but management restated the prior range today.
            kind, detail = _compare_values(cur.prior, cur.current, cur.metric)
            detail = f"{detail} Prior range as stated on this call."
            previous_text = cur.prior
        elif key in withheld_keys:
            kind, previous_text = "not_compared", None
            detail = f"The {previous_period} item failed the checks (see warnings)."
        else:
            kind, detail, previous_text = "new", "No comparable guidance last quarter.", None
        stated = cur.direction
        comparable = ("raised", "lowered", "maintained")
        if kind in comparable and stated in comparable and stated != kind:
            detail += f" Note: management described it as {stated}."
        changes.append(
            GuidanceChange(
                metric=cur.metric,
                period=cur.period,
                previous=previous_text,
                current=cur.current,
                change=kind,
                stated_direction=stated,
                detail=detail,
            )
        )

    figures_by_metric = {metric_key(f.metric): f for f in current_figures}
    for key, prev in prev_by_key.items():
        if key in curr_by_key:
            continue
        metric, period = key
        kind: GuidanceChangeKind
        figure = figures_by_metric.get(metric)
        if key in withheld_keys:
            kind = "not_compared"
            detail = f"The {current_period} item failed the checks (see warnings)."
        elif period == period_key(current_period) and figure is not None:
            # Last quarter guided *this* quarter: compare guidance with the actual result.
            kind = "reported"
            detail = _actual_vs_range(prev.current, figure.value, prev.metric)
        else:
            kind, detail = "dropped", "Not repeated on this call."
        changes.append(
            GuidanceChange(
                metric=prev.metric,
                period=prev.period,
                previous=prev.current,
                current=None,
                change=kind,
                stated_direction=None,
                detail=detail,
            )
        )
    return changes, warnings


# --- Risks ------------------------------------------------------------------------------


def _title_similarity(a: str, b: str) -> float:
    """Jaccard overlap of the words in two risk titles (0 = nothing shared, 1 = same)."""
    words_a, words_b = (
        set(re.findall(r"[a-z]+", a.casefold())),
        set(re.findall(r"[a-z]+", b.casefold())),
    )
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def compare_risks(previous: list[Risk], current: list[Risk]) -> list[RiskChange]:
    """Match risks by category; within a category, pair the most similar titles.

    The category is a fixed list in the schema, so it is stable between two runs in a
    way that free-text titles are not ("Europe slowdown" and "Slower European orders"
    share no word). A pairing whose titles share less than 30% of their words is
    still reported as persisting, but marked ``matched_on="category"`` so the report
    can say the link rests on the category alone. For the catch-all "other" category
    the titles must overlap, because "other" says nothing about the risk.
    """
    changes: list[RiskChange] = []
    categories = list(dict.fromkeys([r.category for r in current + previous]))
    for category in categories:
        prev = [r for r in previous if r.category == category]
        curr = [r for r in current if r.category == category]
        min_similarity = TITLE_MATCH if category == "other" else 0.0
        while prev and curr:
            score, i, j = max(
                (_title_similarity(p.title, c.title), i, j)
                for i, p in enumerate(prev)
                for j, c in enumerate(curr)
            )
            if score < min_similarity:
                break
            p, c = prev.pop(i), curr.pop(j)
            changes.append(
                RiskChange(
                    category=category,
                    change="persisting",
                    previous_title=p.title,
                    current_title=c.title,
                    matched_on="title" if score >= TITLE_MATCH else "category",
                )
            )
        changes += [
            RiskChange(category=category, change="new", previous_title=None, current_title=c.title)
            for c in curr
        ]
        changes += [
            RiskChange(
                category=category, change="dropped", previous_title=p.title, current_title=None
            )
            for p in prev
        ]
    order = {"new": 0, "persisting": 1, "dropped": 2}
    return sorted(changes, key=lambda change: order[change.change])


# --- Tone and figures -------------------------------------------------------------------


def compare_tone(previous: ToneLabel, current: ToneLabel) -> ToneShift:
    steps = TONE_SCALE.index(current) - TONE_SCALE.index(previous)
    if steps == 0:
        description = f"Unchanged ({current})."
    else:
        direction = "more positive" if steps > 0 else "more negative"
        notches = "notch" if abs(steps) == 1 else "notches"
        description = f"{abs(steps)} {notches} {direction}: {previous} -> {current}."
    return ToneShift(previous=previous, current=current, steps=steps, description=description)


def _compare_checked_tone(previous: VerifiedBrief, current: VerifiedBrief) -> ToneShift:
    """Compare tone only if both tone assessments passed quote verification."""
    failed = [b.period for b in (previous, current) if b.tone.status == "unverified"]
    prev_label, curr_label = previous.tone.item.label, current.tone.item.label
    if failed:
        return ToneShift(
            previous=prev_label,
            current=curr_label,
            steps=None,
            description=(
                f"Not compared: the tone quotes for {' and '.join(failed)} could not be "
                f"verified (model labels: {prev_label} -> {curr_label})."
            ),
        )
    return compare_tone(prev_label, curr_label)


def pair_figures(previous: list[HeadlineFigure], current: list[HeadlineFigure]) -> list[FigurePair]:
    prev_by_key = {metric_key(f.metric): f for f in previous}
    pairs = []
    for fig in current:
        prev = prev_by_key.pop(metric_key(fig.metric), None)
        pairs.append(
            FigurePair(metric=fig.metric, previous=prev.value if prev else None, current=fig.value)
        )
    pairs += [
        FigurePair(metric=f.metric, previous=f.value, current=None) for f in prev_by_key.values()
    ]
    return pairs


def compare_briefs(previous: VerifiedBrief, current: VerifiedBrief) -> Comparison:
    warnings: list[str] = []
    p, c = previous.period, current.period
    prev_figures = _usable(previous.headline_figures, "figure", p, warnings)
    curr_figures = _usable(current.headline_figures, "figure", c, warnings)
    withheld: list[GuidanceItem] = []
    prev_guidance = _usable(previous.guidance, "guidance", p, warnings, withheld)
    curr_guidance = _usable(current.guidance, "guidance", c, warnings, withheld)
    guidance, guidance_warnings = compare_guidance(
        prev_guidance, curr_guidance, c, curr_figures, previous_period=p, withheld=withheld
    )
    return Comparison(
        company=current.company,
        previous_period=p,
        current_period=c,
        guidance=guidance,
        risks=compare_risks(
            _usable(previous.risks, "risk", p, warnings),
            _usable(current.risks, "risk", c, warnings),
        ),
        tone=_compare_checked_tone(previous, current),
        figures=pair_figures(prev_figures, curr_figures),
        warnings=warnings + guidance_warnings,
    )
