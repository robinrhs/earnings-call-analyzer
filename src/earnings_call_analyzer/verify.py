"""Check every quote the model returned against the transcript.

This is the core of the project. A language model can produce a fluent, plausible brief
that contains numbers or statements nobody said. In finance that is worse than useless:
an analyst who repeats a fabricated guidance number is wrong in public. So nothing the
model says is trusted until its supporting quote has been found in the transcript.

For each quote we try, in this order (cheapest and strictest first):

1. **exact** - the quote is a literal substring of the cited paragraph that starts and
   ends on a word boundary, so "9% to $82 million" is not found inside "19% to $82
   million", and "3%" is not found inside "-3%".
2. **normalized** - same words in the same order after harmless normalisation:
   curly vs straight quotes, en/em dashes vs hyphens, extra whitespace, upper/lower case,
   punctuation at word edges. "$", "%" and a minus sign in front of a number are kept
   because they change meaning; accounting brackets "(3)%" are read as "-3%".
3. exact/normalized in *another* paragraph - the model copied the words correctly but
   cited the wrong id. We correct the id and mark the item **corrected**.
4. **fuzzy** - a conservative similarity match for small slips (one word changed or
   dropped), first in the cited paragraph, then elsewhere. The threshold is 0.90 on
   :class:`difflib.SequenceMatcher` similarity and only applies to quotes of at least
   eight words, because short strings match each other by chance. The words that
   differ must be harmless: a near match is rejected if any differing word is a number,
   a unit or scale ("million" vs "billion"), a negation ("not") or a direction word
   ("up" vs "down"). Those are exactly the slips that turn a quote into a different claim.
5. Otherwise the quote is **unverified**.

A verified quote proves the words were said. It does not prove the model's *summary*
of them is right, so a second, cheaper check compares the numbers in each headline
figure and guidance item with the numbers in the paragraph where the quote was found.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from .amounts import NEGATIVE_WORDING, find_amounts, signed_numbers
from .schema import AnalystQuestion, Brief, Evidence, GuidanceItem, HeadlineFigure, Risk, Tone
from .transcript import Paragraph, Transcript

Status = Literal["verified", "corrected", "unverified"]
Method = Literal["exact", "normalized", "fuzzy", "none"]
Policy = Literal["drop", "flag"]

# Typographic characters that a model (or a PDF export) swaps freely for ASCII ones,
# given as Unicode code points so the source file itself stays plain ASCII.
_SINGLE_QUOTES = (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)  # curly apostrophes, prime
_DOUBLE_QUOTES = (0x201C, 0x201D, 0x201E, 0x2033)  # curly double quotes, double prime
_DASHES = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)  # hyphens, en/em, minus
_CHAR_MAP: dict[int, str] = {
    **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'),
    **dict.fromkeys(_DASHES, "-"),
    0x00A0: " ",  # non-breaking space
    0x2026: "...",  # ellipsis character
}
# Punctuation stripped from the *edges* of each word. "$" and "%" stay on purpose, and
# a "-" in front of a number is put back (see normalize_word).
_EDGE_PUNCTUATION = "\"'.,;:!?()[]{}<>*_-"
# Accounting brackets around a number mean minus: "(3)%" -> "-3%", "($20.5)" -> "-$20.5".
_ACCOUNTING_NEGATIVE = re.compile(r"\((\$?\d[\d,]*(?:\.\d+)?)\)")
_STARTS_WITH_NUMBER = re.compile(r"\$?\d")
# Characters that belong to the number right after them: currency signs, minus, bracket.
_NUMBER_PREFIX = "$-(" + "".join(map(chr, (0x20AC, 0x00A3, 0x2212)))
_QUOTE_WRAPPERS = "\"'" + "".join(map(chr, _SINGLE_QUOTES[:2] + _DOUBLE_QUOTES[:2]))


@dataclass(frozen=True)
class VerificationSettings:
    """Thresholds, kept in one place so the README and the code cannot drift apart."""

    fuzzy_threshold: float = 0.90
    min_quote_words: int = 5
    min_fuzzy_words: int = 8


DEFAULT_SETTINGS = VerificationSettings()


class QuoteCheck(BaseModel):
    """The result of checking one quote."""

    cited_id: str
    found_id: str | None
    status: Status
    method: Method
    score: float
    matched_text: str | None
    note: str


# --- Normalisation ----------------------------------------------------------------------


def _clean_quote(quote: str) -> str:
    quote = quote.strip()
    while len(quote) >= 2 and quote[0] in _QUOTE_WRAPPERS and quote[-1] in _QUOTE_WRAPPERS:
        quote = quote[1:-1].strip()
    return quote


def normalize_word(word: str) -> str:
    word = unicodedata.normalize("NFKC", word).translate(_CHAR_MAP).casefold()
    word = _ACCOUNTING_NEGATIVE.sub(r"-\1", word)
    core = word.strip(_EDGE_PUNCTUATION)
    # The sign is part of the number: "-3%" must not normalise to the same word as "3%".
    leading = word[: len(word) - len(word.lstrip(_EDGE_PUNCTUATION))]
    if leading.endswith("-") and _STARTS_WITH_NUMBER.match(core):
        core = "-" + core
    return core


def tokenize(text: str) -> list[tuple[str, str]]:
    """Split on whitespace and return (normalized word, original word) pairs.

    Keeping the original lets us show the transcript's exact wording for a fuzzy match.
    Words that are pure punctuation (a lone dash) disappear after normalisation.
    """
    pairs = []
    for original in text.split():
        normalized = normalize_word(original)
        if normalized:
            pairs.append((normalized, original))
    return pairs


def normalize_text(text: str) -> str:
    return " ".join(word for word, _ in tokenize(text))


# --- Matching inside one paragraph ------------------------------------------------------


@dataclass(frozen=True)
class _Match:
    method: Method
    score: float
    text: str


def _starts_cleanly(text: str, start: int) -> bool:
    """A quote must not begin in the middle of a word or a number."""
    if start == 0:
        return True
    before, first = text[start - 1], text[start]
    if before.isalnum() and first.isalnum():
        return False  # "9%" inside "19%", "venue" inside "revenue"
    if first.isdigit() and before in _NUMBER_PREFIX:
        return False  # "3%" from "-3%", "1.62" from "$1.62", "3)" from "(3)"
    if first in _NUMBER_PREFIX and before in _NUMBER_PREFIX:
        return False  # "$20" from "-$20"
    return not (first.isdigit() and before in ".," and start >= 2 and text[start - 2].isdigit())


def _ends_cleanly(text: str, end: int) -> bool:
    """A quote must not stop in the middle of a word or a number."""
    if end == len(text):
        return True
    last, after = text[end - 1], text[end]
    if last.isalnum() and after.isalnum():
        return False  # "$82 m" from "$82 million"
    if last.isdigit() and after in "%)":
        return False  # "grew 19" from "grew 19%"
    return not (
        last.isdigit() and after in ".," and end + 1 < len(text) and text[end + 1].isdigit()
    )


def _contains_whole(text: str, quote: str) -> bool:
    """Literal substring test that only accepts matches on word boundaries."""
    start = text.find(quote)
    while start != -1:
        if _starts_cleanly(text, start) and _ends_cleanly(text, start + len(quote)):
            return True
        start = text.find(quote, start + 1)
    return False


def _exact_or_normalized(quote: str, paragraph: Paragraph) -> _Match | None:
    if quote and _contains_whole(paragraph.text, quote):
        return _Match("exact", 1.0, quote)
    quote_words = [w for w, _ in tokenize(quote)]
    para = tokenize(paragraph.text)
    para_words = [w for w, _ in para]
    n = len(quote_words)
    for start in range(len(para_words) - n + 1):
        if para_words[start : start + n] == quote_words:
            original = " ".join(o for _, o in para[start : start + n])
            return _Match("normalized", 1.0, original)
    return None


def _best_fuzzy(quote: str, paragraph: Paragraph) -> _Match | None:
    """Best similarity between the quote and any similar-length window of the paragraph.

    Windows are a few words shorter or longer than the quote, so a dropped or inserted
    word does not ruin the alignment. Returns the best window even if it is below the
    threshold, so the caller can report how close the model came.
    """
    quote_norm = normalize_text(quote)
    n = len(quote_norm.split())
    para = tokenize(paragraph.text)
    if not para or n == 0:
        return None
    best: _Match | None = None
    for size in range(max(1, n - 2), n + 3):
        for start in range(max(1, len(para) - size + 1)):
            window = para[start : start + size]
            window_norm = " ".join(w for w, _ in window)
            matcher = SequenceMatcher(None, quote_norm, window_norm, autojunk=False)
            if best is not None and matcher.real_quick_ratio() <= best.score:
                continue  # cannot beat the current best, skip the expensive ratio
            score = matcher.ratio()
            if best is None or score > best.score:
                best = _Match("fuzzy", score, " ".join(o for _, o in window))
    return best


# Words whose change turns a quote into a different claim. A fuzzy match may differ
# from the transcript in small words ("the", "that", "our"), never in one of these.
_UNIT_WORDS = {
    "%", "percent", "pp", "bps", "basis", "point", "points", "thousand", "million",
    "millions", "billion", "billions", "trillion", "mn", "bn", "m", "b", "k", "x",
}  # fmt: skip
_NEGATIONS = {"not", "no", "never", "none", "nor", "neither", "without", "cannot", "nothing"}
_DIRECTION_WORDS = {
    "up", "down", "higher", "lower", "above", "below", "more", "less", "increase",
    "increased", "decrease", "decreased", "raise", "raised", "lowered", "grew", "growth",
    "decline", "declined", "gain", "gains", "loss", "losses", "profit", "positive", "negative",
    "rise", "rose", "fall", "fell", "improve", "improved", "worsen", "worsened", "expand",
    "expanded", "contract", "contracted", "beat", "miss", "missed", "ahead", "behind",
}  # fmt: skip


def _changes_meaning(word: str) -> bool:
    return (
        any(ch.isdigit() for ch in word)
        or "$" in word
        or "%" in word
        or word.endswith("n't")  # don't, won't, isn't
        or word in _UNIT_WORDS
        or word in _NEGATIONS
        or word in _DIRECTION_WORDS
    )


def meaningful_differences(quote: str, matched: str) -> list[str]:
    """The word-level differences between two texts that change what is being claimed.

    Returns e.g. ["'million' vs 'billion'"]; an empty list means every difference is a
    harmless small word.
    """
    a = [w for w, _ in tokenize(quote)]
    b = [w for w, _ in tokenize(matched)]
    differences = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if any(_changes_meaning(w) for w in a[i1:i2] + b[j1:j2]):
            ours, theirs = " ".join(a[i1:i2]) or "(nothing)", " ".join(b[j1:j2]) or "(nothing)"
            differences.append(f"'{ours}' vs '{theirs}'")
    return differences


# --- Checking one quote -----------------------------------------------------------------


def _search_order(transcript: Transcript, cited_id: str) -> list[Paragraph]:
    """All paragraphs except the cited one, nearest to the cited position first.

    If the model cites the wrong id, the right paragraph is usually close by, and
    when the same sentence appears twice we prefer the occurrence nearest the citation.
    """
    position = {p.id: i for i, p in enumerate(transcript.paragraphs)}
    cited = cited_id.strip().upper()
    anchor = position.get(cited, 0)
    others = [p for p in transcript.paragraphs if p.id != cited]
    return sorted(others, key=lambda p: abs(position[p.id] - anchor))


def check_quote(
    evidence: Evidence, transcript: Transcript, settings: VerificationSettings = DEFAULT_SETTINGS
) -> QuoteCheck:
    cited_id = evidence.paragraph_id.strip().upper()
    quote = _clean_quote(evidence.quote)
    word_count = len(tokenize(quote))

    if word_count < settings.min_quote_words:
        return QuoteCheck(
            cited_id=cited_id,
            found_id=None,
            status="unverified",
            method="none",
            score=0.0,
            matched_text=None,
            note=f"Quote has {word_count} words; at least {settings.min_quote_words} "
            "are needed to count as evidence.",
        )

    cited = transcript.get(cited_id)
    others = _search_order(transcript, cited_id)
    missing = "" if cited else f"Cited paragraph {cited_id} does not exist. "

    # 1-2. exact or normalized in the cited paragraph
    if cited and (match := _exact_or_normalized(quote, cited)):
        return _result(cited_id, cited_id, "verified", match, "Found in the cited paragraph.")

    # 3. exact or normalized elsewhere: right words, wrong id
    for paragraph in others:
        if match := _exact_or_normalized(quote, paragraph):
            note = f"{missing}Quote found in {paragraph.id}, not in {cited_id}; id corrected."
            return _result(cited_id, paragraph.id, "corrected", match, note.strip())

    # 4. conservative fuzzy fallback, cited paragraph first. A near match that differs
    #    in a number, unit, negation or direction word is *not* accepted: "$1.62 million"
    #    vs "$1.62 billion" is a 97% similar string but a 1,000x different figure, and
    #    "we do expect" vs "we do not expect" says the opposite.
    best_seen: tuple[float, str | None] = (0.0, None)
    meaning_differs: str | None = None
    if word_count >= settings.min_fuzzy_words:
        candidates = ([cited] if cited else []) + others
        for paragraph in candidates:
            match = _best_fuzzy(quote, paragraph)
            if match is None:
                continue
            differences = meaningful_differences(quote, match.text)
            if match.score >= settings.fuzzy_threshold and differences:
                meaning_differs = meaning_differs or (
                    f" A similar sentence exists in {paragraph.id} (similarity "
                    f"{match.score:.2f}), but it differs in a word that changes the claim: "
                    f"{', '.join(differences)}."
                )
            elif match.score >= settings.fuzzy_threshold:
                if paragraph.id == cited_id:
                    note = f"Near-verbatim match (similarity {match.score:.2f})."
                    return _result(cited_id, cited_id, "verified", match, note)
                note = (
                    f"{missing}Near-verbatim match (similarity {match.score:.2f}) in "
                    f"{paragraph.id}, not in {cited_id}; id corrected."
                )
                return _result(cited_id, paragraph.id, "corrected", match, note.strip())
            if match.score > best_seen[0]:
                best_seen = (match.score, paragraph.id)

    # 5. nothing found
    score, where = best_seen
    detail = f" Closest passage: {where} (similarity {score:.2f})." if where else ""
    if meaning_differs:
        detail = meaning_differs
    return QuoteCheck(
        cited_id=cited_id,
        found_id=None,
        status="unverified",
        method="none",
        score=round(score, 3),
        matched_text=None,
        note=f"{missing}Quote not found in the transcript.{detail}".strip(),
    )


def _result(cited_id: str, found_id: str, status: Status, match: _Match, note: str) -> QuoteCheck:
    return QuoteCheck(
        cited_id=cited_id,
        found_id=found_id,
        status=status,
        method=match.method,
        score=round(match.score, 3),
        matched_text=match.text,
        note=note,
    )


# --- Numbers ----------------------------------------------------------------------------


def extract_numbers(text: str) -> list[float]:
    """Signed amounts in a string, without period labels or years.

    '$1,210.5m, down -3% in Q4 2026' -> [1210.5, -3.0]. See :mod:`.amounts`.
    """
    return signed_numbers(text)


def _numbers_missing(claimed: str, source_text: str) -> list[float]:
    """Numbers in the model's summary that the source paragraph does not contain.

    "$1,620 million" is found in a paragraph that says "$1.62 billion" (same amount in
    millions). "-3%" is found in a paragraph that says "a decline of 3%", because the
    sign can be carried by the wording instead of a minus sign.
    """
    source = find_amounts(source_text)
    available = {a.value for a in source} | {
        m for a in source if (m := a.in_millions()) is not None
    }
    if NEGATIVE_WORDING.search(source_text):
        available |= {-value for value in available}
    missing = []
    for amount in find_amounts(claimed):
        if amount.value not in available and amount.in_millions() not in available:
            missing.append(amount.value)
    return missing


# --- Checking a whole brief -------------------------------------------------------------

T = TypeVar("T")

_RANK: dict[Status, int] = {"verified": 0, "corrected": 1, "unverified": 2}


def worst_status(checks: list[QuoteCheck]) -> Status:
    """An item is only as trustworthy as its weakest quote."""
    if not checks:
        return "unverified"
    return max((c.status for c in checks), key=_RANK.__getitem__)


class Checked(BaseModel, Generic[T]):
    """An extracted item together with the result of checking its quotes."""

    item: T
    checks: list[QuoteCheck]
    status: Status
    warnings: list[str]


class RejectedItem(BaseModel):
    section: str
    label: str
    checks: list[QuoteCheck]


class VerifiedBrief(BaseModel):
    company: str
    period: str
    headline_figures: list[Checked[HeadlineFigure]]
    guidance: list[Checked[GuidanceItem]]
    tone: Checked[Tone]
    risks: list[Checked[Risk]]
    analyst_questions: list[Checked[AnalystQuestion]]
    rejected: list[RejectedItem]
    policy: Policy
    fuzzy_threshold: float

    def all_checks(self) -> list[QuoteCheck]:
        items: list[Checked] = [
            *self.headline_figures,
            *self.guidance,
            self.tone,
            *self.risks,
            *self.analyst_questions,
        ]
        checks = [c for item in items for c in item.checks]
        return checks + [c for r in self.rejected for c in r.checks]

    def counts(self) -> dict[str, int]:
        result = {"verified": 0, "corrected": 0, "unverified": 0}
        for check in self.all_checks():
            result[check.status] += 1
        return result


def _check_all(
    evidences: list[Evidence], transcript: Transcript, settings: VerificationSettings
) -> list[QuoteCheck]:
    return [check_quote(e, transcript, settings) for e in evidences]


def _source_text(check: QuoteCheck, transcript: Transcript) -> str:
    paragraph = transcript.get(check.found_id) if check.found_id else None
    return paragraph.text if paragraph else ""


def _number_warnings(
    claimed: str, field: str, check: QuoteCheck, transcript: Transcript
) -> list[str]:
    if check.status == "unverified":
        return []
    missing = _numbers_missing(claimed, _source_text(check, transcript))
    if not missing:
        return []
    shown = ", ".join(f"{n:g}" for n in missing)
    return [f"{field} contains {shown}, which does not appear in {check.found_id}."]


def _speaker_warnings(
    question: AnalystQuestion, checks: list[QuoteCheck], transcript: Transcript
) -> list[str]:
    """The question should come from an analyst and the answer from management."""
    warnings = []
    expected = (("question", "analyst"), ("answer", "management"))
    for check, (part, role) in zip(checks, expected, strict=True):
        paragraph = transcript.get(check.found_id) if check.found_id else None
        if paragraph and paragraph.role != role:
            warnings.append(
                f"The {part} quote is from {paragraph.speaker or 'an unknown speaker'} "
                f"({paragraph.role}), not from {role}."
            )
    question_paragraph = transcript.get(checks[0].found_id) if checks[0].found_id else None
    if question_paragraph and question_paragraph.speaker:
        surname = question.analyst.split()[-1].casefold() if question.analyst.split() else ""
        if surname and surname not in question_paragraph.speaker.casefold():
            warnings.append(
                f"Question attributed to {question.analyst}, but {question_paragraph.id} "
                f"is spoken by {question_paragraph.speaker}."
            )
    return warnings


def verify_brief(
    brief: Brief,
    transcript: Transcript,
    policy: Policy = "drop",
    settings: VerificationSettings = DEFAULT_SETTINGS,
) -> VerifiedBrief:
    """Check every quote in the brief and apply the policy for unverified items.

    ``drop``: unverified items leave the brief and are listed under "rejected", so a
    reader can still see what the model claimed and why it was not accepted.
    ``flag``: unverified items stay in the brief with a red badge.
    The tone assessment is never dropped (a brief needs one); it is flagged instead.
    """
    rejected: list[RejectedItem] = []

    def keep(section: str, label: str, checked: Checked) -> bool:
        if checked.status == "unverified" and policy == "drop":
            rejected.append(RejectedItem(section=section, label=label, checks=checked.checks))
            return False
        return True

    figures = []
    for fig in brief.headline_figures:
        checks = _check_all([fig.evidence], transcript, settings)
        checked = Checked[HeadlineFigure](
            item=fig, checks=checks, status=worst_status(checks), warnings=[]
        )
        checked.warnings += _number_warnings(fig.value, "Value", checked.checks[0], transcript)
        if keep("Headline figures", f"{fig.metric}: {fig.value}", checked):
            figures.append(checked)

    guidance = []
    for g in brief.guidance:
        checks = _check_all([g.evidence], transcript, settings)
        checked = Checked[GuidanceItem](
            item=g, checks=checks, status=worst_status(checks), warnings=[]
        )
        checked.warnings += _number_warnings(g.current, "Guidance", checked.checks[0], transcript)
        if keep("Guidance", f"{g.metric} ({g.period}): {g.current}", checked):
            guidance.append(checked)

    tone_checks = _check_all(brief.tone.evidence, transcript, settings)
    tone = Checked[Tone](
        item=brief.tone, checks=tone_checks, status=worst_status(tone_checks), warnings=[]
    )

    risks = []
    for r in brief.risks:
        checks = _check_all([r.evidence], transcript, settings)
        checked = Checked[Risk](item=r, checks=checks, status=worst_status(checks), warnings=[])
        if keep("Risks", r.title, checked):
            risks.append(checked)

    questions = []
    for q in brief.analyst_questions:
        checks = _check_all([q.question_evidence, q.answer_evidence], transcript, settings)
        checked = Checked[AnalystQuestion](
            item=q, checks=checks, status=worst_status(checks), warnings=[]
        )
        checked.warnings += _speaker_warnings(q, checked.checks, transcript)
        if keep("Analyst questions", f"{q.analyst}: {q.topic}", checked):
            questions.append(checked)

    return VerifiedBrief(
        company=brief.company,
        period=brief.period,
        headline_figures=figures,
        guidance=guidance,
        tone=tone,
        risks=risks,
        analyst_questions=questions,
        rejected=rejected,
        policy=policy,
        fuzzy_threshold=settings.fuzzy_threshold,
    )


__all__ = [
    "Checked",
    "QuoteCheck",
    "RejectedItem",
    "VerificationSettings",
    "VerifiedBrief",
    "check_quote",
    "extract_numbers",
    "meaningful_differences",
    "normalize_text",
    "verify_brief",
    "worst_status",
]
