"""Quarter-over-quarter comparison of two verified briefs.

Analysts read a call against the previous one. The questions are always the same:

* **Guidance** - raised, lowered or maintained? Did a quarter we had guidance for come
  in above, within or below that range?
* **Risks** - which concerns are new, which persist, which disappeared?
* **Tone** - is management more or less confident than three months ago?

Only items that passed verification (verified or corrected) are compared, so the diff
never rests on a quote that could not be found.

Everything here is plain rule-based logic on the two briefs: no second model call.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from .schema import GuidanceItem, HeadlineFigure, Risk, ToneLabel
from .verify import Checked, VerifiedBrief

GuidanceChangeKind = Literal[
    "raised", "lowered", "maintained", "changed", "new", "dropped", "reported"
]
RiskChangeKind = Literal["new", "persisting", "dropped"]

# Ordered from most negative to most positive, so a tone shift is a difference of ranks.
TONE_SCALE: list[ToneLabel] = ["defensive", "cautious", "balanced", "constructive", "confident"]

# A change smaller than this (relative to the midpoint) counts as "maintained".
MAINTAINED_TOLERANCE = 0.005

_UNIT_SCALE = {
    "thousand": 1e-3,
    "k": 1e-3,
    "million": 1.0,
    "mn": 1.0,
    "m": 1.0,
    "billion": 1e3,
    "bn": 1e3,
    "b": 1e3,
}
_AMOUNT = re.compile(
    r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|(?:thousand|million|billion|mn|bn|k|m|b)\b)?",
    re.IGNORECASE,
)
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


class ToneShift(BaseModel):
    previous: ToneLabel
    current: ToneLabel
    steps: int
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


# --- Parsing numbers and keys -----------------------------------------------------------


def parse_amounts(text: str) -> tuple[list[float], str | None]:
    """Numbers in a guidance string, scaled to millions, plus the unit kind.

    "$1.62 to $1.66 billion" -> ([1620.0, 1660.0], "money"). A number without its own
    unit takes the next unit in the string, the usual shorthand for ranges.
    Percentages are returned as is with unit kind "percent".
    """
    found = [(float(n.replace(",", "")), (u or "").lower()) for n, u in _AMOUNT.findall(text)]
    if not found:
        return [], None
    if any(unit == "%" for _, unit in found):
        return [value for value, unit in found if unit == "%"], "percent"
    values = []
    for i, (value, unit) in enumerate(found):
        if not unit:
            unit = next((u for _, u in found[i + 1 :] if u), "")
        values.append(value * _UNIT_SCALE.get(unit, 1.0))
    return values, "money"


def midpoint(text: str) -> tuple[float, str] | None:
    """Midpoint of a range (or the single number) and its unit kind."""
    values, kind = parse_amounts(text)
    if not values or kind is None:
        return None
    return (min(values) + max(values)) / 2, kind


def metric_key(metric: str) -> str:
    words = re.findall(r"[a-z]+", metric.casefold())
    return " ".join(sorted(w for w in words if w not in _STOPWORDS))


def period_key(period: str) -> str:
    """'Q4 FY2026', 'fourth quarter fiscal 2026' -> 'Q4 2026'; 'FY2026' -> 'FY 2026'."""
    text = period.casefold()
    year_match = re.search(r"(?:fy\s?)?(20\d{2})|fy\s?(\d{2})\b", text)
    year = ""
    if year_match:
        year = year_match.group(1) or f"20{year_match.group(2)}"
    quarter_words = {"first": "1", "second": "2", "third": "3", "fourth": "4"}
    quarter = None
    if q := re.search(r"\bq([1-4])\b", text):
        quarter = q.group(1)
    else:
        for word, number in quarter_words.items():
            if f"{word} quarter" in text:
                quarter = number
    return f"Q{quarter} {year}".strip() if quarter else f"FY {year}".strip()


def _compare_values(previous: str, current: str) -> tuple[GuidanceChangeKind, str]:
    prev_mid, curr_mid = midpoint(previous), midpoint(current)
    if prev_mid is None or curr_mid is None or prev_mid[1] != curr_mid[1]:
        same = re.sub(r"\s+", " ", previous.casefold()) == re.sub(r"\s+", " ", current.casefold())
        return (
            ("maintained", "Same wording.") if same else ("changed", "Not comparable as numbers.")
        )
    (p, kind), (c, _) = prev_mid, curr_mid
    if p == 0 or abs(c - p) / abs(p) < MAINTAINED_TOLERANCE:
        return "maintained", "Midpoint unchanged."
    delta = c - p
    if kind == "percent":
        # Margins are compared in percentage points (pp), not in percent of percent.
        detail = f"Midpoint {p:.4g}% -> {c:.4g}% ({delta:+.4g} pp)."
    else:
        detail = (
            f"Midpoint {p:,.4g} -> {c:,.4g} million ({delta:+,.4g} million, {delta / abs(p):+.1%})."
        )
    return ("raised" if delta > 0 else "lowered"), detail


# --- Guidance ---------------------------------------------------------------------------


def _usable(items: list[Checked]) -> list:
    return [c.item for c in items if c.status != "unverified"]


def _actual_vs_range(guided: str, actual: str) -> str:
    """Where a reported number landed relative to the range guided last quarter."""
    values, kind = parse_amounts(guided)
    actual_values, actual_kind = parse_amounts(actual)
    if not values or not actual_values or kind != actual_kind:
        return f"Reported {actual} vs guided {guided}."
    low, high, value = min(values), max(values), actual_values[0]
    if value > high:
        position = "above the guided range"
    elif value < low:
        position = "below the guided range"
    elif value >= (low + high) / 2:
        position = "within the guided range (upper half)"
    else:
        position = "within the guided range (lower half)"
    return f"Reported {actual}, {position} of {guided}."


def compare_guidance(
    previous: list[GuidanceItem],
    current: list[GuidanceItem],
    current_period: str,
    current_figures: list[HeadlineFigure],
) -> list[GuidanceChange]:
    prev_by_key = {(metric_key(g.metric), period_key(g.period)): g for g in previous}
    curr_by_key = {(metric_key(g.metric), period_key(g.period)): g for g in current}
    changes: list[GuidanceChange] = []

    for key, cur in curr_by_key.items():
        prev = prev_by_key.get(key)
        if prev is not None:
            kind, detail = _compare_values(prev.current, cur.current)
            previous_text = prev.current
        elif cur.prior:
            # Not in last quarter's brief, but management restated the prior range today.
            kind, detail = _compare_values(cur.prior, cur.current)
            detail = f"{detail} Prior range as stated on this call."
            previous_text = cur.prior
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
        figure = figures_by_metric.get(metric)
        if period == period_key(current_period) and figure is not None:
            # Last quarter guided *this* quarter: compare guidance with the actual result.
            kind: GuidanceChangeKind = "reported"
            detail = _actual_vs_range(prev.current, figure.value)
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
    return changes


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
    way that free-text titles are not. For the catch-all "other" category the titles
    must share at least 30% of their words to count as the same risk.
    """
    changes: list[RiskChange] = []
    categories = list(dict.fromkeys([r.category for r in current + previous]))
    for category in categories:
        prev = [r for r in previous if r.category == category]
        curr = [r for r in current if r.category == category]
        min_similarity = 0.3 if category == "other" else 0.0
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
    prev_figures = _usable(previous.headline_figures)
    curr_figures = _usable(current.headline_figures)
    return Comparison(
        company=current.company,
        previous_period=previous.period,
        current_period=current.period,
        guidance=compare_guidance(
            _usable(previous.guidance), _usable(current.guidance), current.period, curr_figures
        ),
        risks=compare_risks(_usable(previous.risks), _usable(current.risks)),
        tone=compare_tone(previous.tone.item.label, current.tone.item.label),
        figures=pair_figures(prev_figures, curr_figures),
    )
