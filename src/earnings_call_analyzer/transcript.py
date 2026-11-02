"""Load an earnings call transcript and split it into numbered, speaker-tagged paragraphs.

Why this matters for the analysis: an earnings call has two very different parts.

* **Prepared remarks** are scripted and reviewed by lawyers and investor relations.
  They tell you the story management *wants* to tell.
* **Q&A** is unscripted. Analysts probe the weak spots, and how management answers
  (directly, partially, or not at all) is often more informative than the script.

So every paragraph gets three tags: a stable id (``P012``) that the language model must
cite, the speaker, and the section. The id is what makes every later claim checkable.

The heuristics below are deliberately simple and documented. They cover the common
layouts ("Name -- Title" on its own line, "Name: text" inline, a participants list,
explicit section headers, and an operator who announces the Q&A). Anything they cannot
classify is tagged ``unknown`` rather than guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Section = Literal["front_matter", "prepared_remarks", "qa"]
Role = Literal["operator", "management", "analyst", "unknown"]

# --- Section headers -------------------------------------------------------------------
# Headers are matched after stripping decoration such as "=== ... ===" or "**...**".
PREPARED_HEADER = re.compile(
    r"^(prepared remarks|presentation|opening remarks|management remarks)( section)?$"
)
QA_HEADER = re.compile(
    r"^(question[- ]and[- ]answer( session)?|questions? (and|&) answers?|q ?& ?a( session)?)$"
)
PARTICIPANTS_HEADER = re.compile(
    r"^(corporate participants|conference call participants|call participants|participants"
    r"|company participants|executives|analysts|management|company)$"
)
# Only these may open the participants list; "management"/"company" are sub-headers.
PARTICIPANTS_OPENERS = {
    "corporate participants",
    "conference call participants",
    "call participants",
    "participants",
    "company participants",
    "executives",
    "analysts",
}

MANAGEMENT_LIST_HEADERS = {
    "corporate participants",
    "company participants",
    "executives",
    "management",
    "company",
}

# The operator usually hands over to Q&A with a sentence like this. If a transcript has
# no explicit Q&A header, the first operator paragraph that matches starts the Q&A.
# Future tense in the welcome ("there will be a question-and-answer session") must not
# match, so the cue needs "first question" or an explicit "now begin/open ...".
QA_CUE = re.compile(
    r"\b(first|next) question\b"
    r"|\bnow (begin|open|start|conduct) the (question-and-answer|question and answer|q&a)"
    r"|\bopen (up )?the (line|lines|call|floor) (for|to) questions",
    re.IGNORECASE,
)

# "Our first question comes from Priya Raman with Halcyon Securities." -> an analyst.
OPERATOR_INTRO = re.compile(
    r"(?:question|line)\s+(?:comes|is)\s+from\s+(?:the\s+line\s+of\s+)?"
    r"(?P<name>[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){1,3})"
)

# Title keywords used to decide who is management and who is an analyst.
ANALYST_WORDS = ("analyst", "research")
MANAGEMENT_WORDS = (
    "chief",
    "ceo",
    "cfo",
    "coo",
    "cto",
    "president",
    "officer",
    "vice president",
    "vp",
    "head of",
    "investor relations",
    "treasurer",
    "founder",
    "chair",
    "director",
)

# Words that look like capitalised names but are titles of the document itself.
NOT_A_NAME = {
    "inc",
    "inc.",
    "corp",
    "corp.",
    "ag",
    "plc",
    "ltd",
    "ltd.",
    "llc",
    "group",
    "holdings",
    "earnings",
    "call",
    "conference",
    "transcript",
    "quarter",
    "results",
    "thank",
    "good",
    "safe",
    "harbor",
    "disclaimer",
}
NAME_PARTICLES = {"de", "da", "van", "von", "der", "di", "du", "le", "la", "bin", "al"}
# " -- ", em dash, en dash, hyphen and comma, in that order of preference.
SPEAKER_SEPARATORS = (" -- ", " \u2014 ", " \u2013 ", " - ", ", ")
PAGE_NUMBER_LINE = re.compile(r"^\s*(page\s+)?\d+(\s*(of|/)\s*\d+)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Paragraph:
    """One paragraph of the transcript, the unit that the model cites."""

    id: str
    text: str
    speaker: str | None
    role: Role
    section: Section


@dataclass(frozen=True)
class Transcript:
    paragraphs: tuple[Paragraph, ...]
    source: str = ""
    _index: dict[str, Paragraph] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        self._index.update({p.id: p for p in self.paragraphs})

    def get(self, paragraph_id: str) -> Paragraph | None:
        return self._index.get(paragraph_id.strip().upper())

    def ids(self) -> list[str]:
        return [p.id for p in self.paragraphs]


# --- Loading ----------------------------------------------------------------------------


def load_text(path: str | Path) -> str:
    """Read a .txt or .pdf transcript and return plain text."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return clean_pdf_text("\n".join(pages))
    raise ValueError(f"Unsupported transcript format '{suffix}'. Use .txt or .pdf.")


def clean_pdf_text(text: str) -> str:
    """Undo the two most common PDF artefacts: page-number lines and hyphenated wraps."""
    lines = [line for line in text.splitlines() if not PAGE_NUMBER_LINE.match(line)]
    joined = "\n".join(lines)
    # "manage-\nment" -> "management" (only when the next line starts in lower case,
    # so real hyphenated words such as "year-\nover-year" at a line end are rare misses).
    return re.sub(r"(\w)-\n([a-z])", r"\1\2", joined)


def load_transcript(path: str | Path) -> Transcript:
    return parse_transcript(load_text(path), source=str(path))


# --- Small classifiers ------------------------------------------------------------------


def _header_key(line: str) -> str:
    """Strip decoration so "=== PREPARED REMARKS ===" becomes "prepared remarks"."""
    return re.sub(r"\s+", " ", line.strip(" \t=-*#_:[]|").casefold())


def _looks_like_name(text: str) -> bool:
    tokens = text.split()
    if not 1 <= len(tokens) <= 5 or len(text) > 50:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    for token in tokens:
        if token.casefold() in NOT_A_NAME:
            return False
        if token in NAME_PARTICLES:
            continue
        if not token[0].isupper():
            return False
    return True


def _role_from_title(title: str) -> Role:
    lowered = title.casefold()
    if any(word in lowered for word in ANALYST_WORDS):
        return "analyst"
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in MANAGEMENT_WORDS):
        return "management"
    return "unknown"


@dataclass(frozen=True)
class _SpeakerLine:
    name: str
    title: str | None
    rest: str | None  # text after "Name:" for inline speaker lines
    form: Literal["titled", "bare", "inline"]


def _match_speaker_line(line: str) -> _SpeakerLine | None:
    """Recognise the three common speaker-line layouts (without deciding if we trust it)."""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    # Inline: "Dana Whitfield: Thank you, operator."
    inline = re.match(r"^([^:]{1,50}):\s+(\S.*)$", stripped)
    if inline and _looks_like_name(inline.group(1)):
        return _SpeakerLine(inline.group(1).strip(), None, inline.group(2), "inline")
    # Titled: "Dana Whitfield -- Chief Executive Officer"
    for sep in SPEAKER_SEPARATORS:
        if sep in stripped:
            name, title = stripped.split(sep, 1)
            if _looks_like_name(name) and len(title.split()) <= 12:
                return _SpeakerLine(name.strip(), title.strip(), None, "titled")
            break
    # Bare: "Operator" or "Dana Whitfield" alone on a line (no closing punctuation).
    bare = stripped.rstrip(":").strip()
    if bare and _looks_like_name(bare) and bare[-1] not in ".,;!?":
        return _SpeakerLine(bare, None, None, "bare")
    return None


# --- Parsing ----------------------------------------------------------------------------


def _collect_roster(lines: list[str]) -> dict[str, Role]:
    """First pass: who is on the call, and in which role?

    Sources, in order of trust: the participants list, names the operator introduces
    ("question comes from ..."), titles in "Name -- Title" lines, and names that appear
    as speakers at least twice (a single "Gross Margin: 48%" line is not a speaker).
    """
    roster: dict[str, Role] = {"operator": "operator"}
    in_participants = False
    default_role: Role = "unknown"
    candidate_counts: Counter[str] = Counter()

    for line in lines:
        key = _header_key(line)
        if PREPARED_HEADER.match(key) or QA_HEADER.match(key):
            in_participants = False
        elif PARTICIPANTS_HEADER.match(key) and (in_participants or key in PARTICIPANTS_OPENERS):
            in_participants = True
            if key in {"analysts", "conference call participants"}:
                default_role = "analyst"
            elif key in MANAGEMENT_LIST_HEADERS:
                default_role = "management"
            else:
                default_role = "unknown"
            continue

        speaker = _match_speaker_line(line)
        if in_participants and speaker and speaker.form != "inline":
            title_role = _role_from_title(speaker.title or "")
            roster[speaker.name.casefold()] = (
                title_role if title_role != "unknown" else default_role
            )
            continue
        if in_participants and line.strip() and not speaker:
            in_participants = False  # a prose line ends the list

        for match in OPERATOR_INTRO.finditer(line):
            roster.setdefault(match.group("name").casefold(), "analyst")
        if speaker:
            candidate_counts[speaker.name.casefold()] += 1
            if speaker.title:
                title_role = _role_from_title(speaker.title)
                if title_role != "unknown":
                    roster.setdefault(speaker.name.casefold(), title_role)

    for name, count in candidate_counts.items():
        if count >= 2:
            roster.setdefault(name, "unknown")
    return roster


def _format_id(number: int, total: int) -> str:
    width = max(3, len(str(total)))
    return f"P{number:0{width}d}"


def parse_transcript(text: str, source: str = "") -> Transcript:
    """Split transcript text into paragraphs tagged with id, speaker, role and section."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    roster = _collect_roster(lines)
    # An explicit "Question-and-Answer Session" header beats the operator-cue heuristic.
    has_qa_header = any(QA_HEADER.match(_header_key(line)) for line in lines)

    # Each raw paragraph: (text, speaker, role, section)
    raw: list[tuple[str, str | None, Role, Section]] = []
    block: list[str] = []
    section: Section = "front_matter"
    in_participants = False
    speaker: str | None = None
    role: Role = "unknown"
    previous_blank = True

    def flush() -> None:
        nonlocal section
        if not block:
            return
        paragraph_text = " ".join(part.strip() for part in block if part.strip())
        block.clear()
        if not paragraph_text:
            return
        if role == "operator" and not has_qa_header and QA_CUE.search(paragraph_text):
            section = "qa"
        raw.append((paragraph_text, speaker, role, section))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            previous_blank = True
            continue

        key = _header_key(stripped)
        if PREPARED_HEADER.match(key):
            flush()
            section, in_participants = "prepared_remarks", False
            previous_blank = True
            continue
        if QA_HEADER.match(key):
            flush()
            section, in_participants = "qa", False
            previous_blank = True
            continue
        if PARTICIPANTS_HEADER.match(key) and (in_participants or key in PARTICIPANTS_OPENERS):
            flush()
            in_participants = True
            previous_blank = True
            continue

        match = _match_speaker_line(stripped)
        if in_participants:
            if match and match.form != "inline":
                continue  # roster entry, already handled in the first pass
            in_participants = False

        if match and _trust_speaker_line(match, roster, previous_blank):
            flush()
            speaker = match.name
            role = _resolve_role(match, roster, section)
            if section == "front_matter":
                section = "prepared_remarks"  # the call has started
            if match.rest:
                block.append(match.rest)
            previous_blank = False
            continue

        block.append(stripped)
        previous_blank = False
    flush()

    total = len(raw)
    paragraphs = tuple(
        Paragraph(id=_format_id(i, total), text=t, speaker=s, role=r, section=sec)
        for i, (t, s, r, sec) in enumerate(raw, start=1)
    )
    return Transcript(paragraphs=paragraphs, source=source)


def _trust_speaker_line(match: _SpeakerLine, roster: dict[str, Role], at_block_start: bool) -> bool:
    """Decide whether a line that *looks* like a speaker line really is one.

    Known names (participants list, operator intros, repeated speakers) are always
    trusted. Unknown names are trusted only on their own line at the start of a block
    and only if they have at least two words, so wrapped prose is not mistaken for a
    speaker. Inline "Name: text" needs a known name.
    """
    known = match.name.casefold() in roster
    if known:
        return True
    if match.form == "inline":
        return False
    return at_block_start and len(match.name.split()) >= 2


def _resolve_role(match: _SpeakerLine, roster: dict[str, Role], section: Section) -> Role:
    if match.name.casefold() == "operator":
        return "operator"
    role = roster.get(match.name.casefold(), "unknown")
    if role == "unknown" and match.title:
        role = _role_from_title(match.title)
    if role == "unknown" and section in ("front_matter", "prepared_remarks"):
        # Prepared remarks are delivered by the company (CEO, CFO, IR), not by analysts.
        role = "management"
    return role


# --- Rendering for the model ------------------------------------------------------------


SECTION_LABELS: dict[Section, str] = {
    "front_matter": "front matter",
    "prepared_remarks": "prepared remarks",
    "qa": "Q&A",
}


def format_for_prompt(transcript: Transcript) -> str:
    """Render the transcript with paragraph ids so the model can cite them.

    Example line::

        [P012] Dana Whitfield (management, prepared remarks): Revenue grew ...
    """
    lines = []
    for p in transcript.paragraphs:
        who = p.speaker or "Unknown speaker"
        lines.append(f"[{p.id}] {who} ({p.role}, {SECTION_LABELS[p.section]}): {p.text}")
    return "\n\n".join(lines)
