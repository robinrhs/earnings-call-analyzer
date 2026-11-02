"""Check every quote the model returned against the transcript.

This is the core of the project. A language model can produce a fluent, plausible brief
that contains numbers or statements nobody said. In finance that is worse than useless:
an analyst who repeats a fabricated guidance number is wrong in public. So nothing the
model says is trusted until its supporting quote has been found in the transcript.

For each quote we try, in this order (cheapest and strictest first):

1. **exact** - the quote is a literal substring of the cited paragraph.
2. **normalized** - same words in the same order after harmless normalisation:
   curly vs straight quotes, en/em dashes vs hyphens, extra whitespace, upper/lower case,
   punctuation at word edges. "$" and "%" are kept because they change meaning.
3. exact/normalized in *another* paragraph - the model copied the words correctly but
   cited the wrong id. We correct the id and mark the item **corrected**.
4. **fuzzy** - a conservative similarity match for small slips (one word changed or
   dropped), first in the cited paragraph, then elsewhere. The threshold is 0.90 on
   :class:`difflib.SequenceMatcher` similarity and only applies to quotes of at least
   eight words, because short strings match each other by chance.
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
# Punctuation stripped from the *edges* of each word. "$" and "%" stay on purpose.
_EDGE_PUNCTUATION = "\"'.,;:!?()[]{}<>*_-"
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
    word = unicodedata.normalize("NFKC", word).translate(_CHAR_MAP)
    return word.casefold().strip(_EDGE_PUNCTUATION)


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


def _exact_or_normalized(quote: str, paragraph: Paragraph) -> _Match | None:
    if quote in paragraph.text:
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

    # 4. conservative fuzzy fallback, cited paragraph first. A near match whose numbers
    #    differ is *not* accepted: "$1.70 billion" vs "$1.62 billion" is a 97% similar
    #    string but a completely different guidance number.
    best_seen: tuple[float, str | None] = (0.0, None)
    numbers_differ_in: str | None = None
    if word_count >= settings.min_fuzzy_words:
        candidates = ([cited] if cited else []) + others
        for paragraph in candidates:
            match = _best_fuzzy(quote, paragraph)
            if match is None:
                continue
            if match.score >= settings.fuzzy_threshold and not _same_numbers(quote, match.text):
                numbers_differ_in = numbers_differ_in or paragraph.id
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
    if numbers_differ_in:
        detail = f" A similar sentence exists in {numbers_differ_in}, but its numbers differ."
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

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """All numbers in a string, with thousands separators removed: '$1,210.5m' -> 1210.5."""
    return [float(raw.replace(",", "")) for raw in _NUMBER.findall(text)]


def _same_numbers(quote: str, transcript_text: str) -> bool:
    return sorted(extract_numbers(quote)) == sorted(extract_numbers(transcript_text))


def _numbers_missing(claimed: str, source_text: str) -> list[float]:
    available = set(extract_numbers(source_text))
    return [n for n in extract_numbers(claimed) if n not in available]


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
    "normalize_text",
    "verify_brief",
    "worst_status",
]
