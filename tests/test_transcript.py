from pathlib import Path

import pytest

from earnings_call_analyzer import transcript as tr
from earnings_call_analyzer.transcript import (
    clean_pdf_text,
    format_for_prompt,
    load_text,
    load_transcript,
    parse_transcript,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

TITLED = """\
Example Corp (fictional) Q1 Earnings Call

Corporate Participants
Jane Doe -- Chief Executive Officer
Analysts
Sam Lee -- Acme Securities

Prepared Remarks

Operator

Welcome to the call. There will be a question-and-answer session later.

Jane Doe -- Chief Executive Officer

Revenue grew 10% to $100 million.
This line is a hard wrap of the same paragraph.

We are raising guidance.

Question-and-Answer Session

Operator

Our first question comes from Sam Lee with Acme Securities.

Sam Lee -- Acme Securities

What drove the margin?

Jane Doe -- Chief Executive Officer

Mix.
"""

INLINE = """\
Operator: Good morning and welcome.
Jane Doe: Thanks. Revenue grew 10%.
Jane Doe: We are maintaining guidance.
Operator: We will now begin the question-and-answer session.
Operator: Our first question comes from Sam Lee of Acme Securities.
Sam Lee: Why did margins fall?
Jane Doe: Freight costs.

Gross Margin: 48%
"""


def test_paragraph_ids_are_sequential_and_zero_padded():
    t = parse_transcript(TITLED)
    assert t.ids() == [f"P{i:03d}" for i in range(1, len(t.paragraphs) + 1)]
    assert t.get("p002") is not None  # lookups are case-insensitive


def test_ids_are_stable_across_runs():
    assert parse_transcript(TITLED) == parse_transcript(TITLED)


def test_hard_wrapped_lines_join_into_one_paragraph():
    t = parse_transcript(TITLED)
    revenue = next(p for p in t.paragraphs if p.text.startswith("Revenue grew"))
    assert revenue.text.endswith("hard wrap of the same paragraph.")


def test_participants_list_is_not_turned_into_paragraphs():
    t = parse_transcript(TITLED)
    assert not any(p.text == "Chief Executive Officer" for p in t.paragraphs)
    assert all("--" not in p.text for p in t.paragraphs)


def test_speakers_roles_and_sections_titled_layout():
    t = parse_transcript(TITLED)
    tags = [(p.speaker, p.role, p.section) for p in t.paragraphs]
    assert tags[0] == (None, "unknown", "front_matter")
    assert tags[1] == ("Operator", "operator", "prepared_remarks")
    assert tags[2] == ("Jane Doe", "management", "prepared_remarks")
    assert ("Sam Lee", "analyst", "qa") in tags
    assert tags[-1] == ("Jane Doe", "management", "qa")


def test_future_tense_welcome_does_not_start_qa():
    t = parse_transcript(TITLED)
    welcome = next(p for p in t.paragraphs if p.text.startswith("Welcome"))
    assert welcome.section == "prepared_remarks"


def test_inline_layout_uses_operator_cue_for_qa():
    t = parse_transcript(INLINE)
    by_text = {p.text: p for p in t.paragraphs}
    assert by_text["Thanks. Revenue grew 10%."].section == "prepared_remarks"
    assert by_text["Why did margins fall?"].section == "qa"
    assert by_text["Why did margins fall?"].role == "analyst"
    assert by_text["Freight costs."].speaker == "Jane Doe"


def test_one_off_label_with_colon_is_not_a_speaker():
    t = parse_transcript(INLINE)
    last = t.paragraphs[-1]
    assert last.text == "Gross Margin: 48%"
    assert last.speaker == "Jane Doe"  # still part of Jane's turn


def test_wrapped_prose_line_is_not_mistaken_for_speaker():
    text = "Jane Doe -- CEO\n\nWe grew fastest in\nNorth America\nthis quarter.\n"
    t = parse_transcript(text)
    assert len(t.paragraphs) == 1
    assert t.paragraphs[0].text == "We grew fastest in North America this quarter."


def test_unknown_role_in_prepared_remarks_defaults_to_management():
    text = "Prepared Remarks\n\nAlex Kim\n\nWe had a good quarter.\n"
    t = parse_transcript(text)
    assert t.paragraphs[0].speaker == "Alex Kim"
    assert t.paragraphs[0].role == "management"


def test_format_for_prompt_contains_ids_speakers_and_sections():
    t = parse_transcript(TITLED)
    rendered = format_for_prompt(t)
    assert "[P003] Jane Doe (management, prepared remarks): Revenue grew 10%" in rendered
    assert "(analyst, Q&A)" in rendered


def test_clean_pdf_text_removes_page_numbers_and_dehyphenates():
    raw = "Our manage-\nment team\nPage 3 of 12\n7\ndelivered."
    assert clean_pdf_text(raw) == "Our management team\ndelivered."


def test_load_text_pdf_uses_pypdf(monkeypatch, tmp_path):
    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage("Operator: Hello and wel-\ncome."), FakePage("Page 2")]

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    pdf = tmp_path / "call.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    assert load_text(pdf).strip() == "Operator: Hello and welcome."


def test_load_text_rejects_other_formats(tmp_path):
    path = tmp_path / "call.docx"
    path.write_text("x")
    with pytest.raises(ValueError, match="Unsupported"):
        load_text(path)


def test_example_transcript_structure():
    t = load_transcript(EXAMPLES / "fernhill_q3_fy2026.txt")
    sections = [p.section for p in t.paragraphs]
    # Sections appear in order: front matter, then prepared remarks, then Q&A.
    assert sections == sorted(sections, key=["front_matter", "prepared_remarks", "qa"].index)
    analysts = {p.speaker for p in t.paragraphs if p.role == "analyst"}
    assert analysts == {
        "Priya Raman",
        "Marcus Oyelaran",
        "Elena Sokolova",
        "Tom Achterberg",
        "Kenji Moreau",
    }
    assert all(p.role != "unknown" for p in t.paragraphs if p.section != "front_matter")


def test_role_from_title():
    assert tr._role_from_title("Chief Financial Officer") == "management"
    assert tr._role_from_title("Equity Research Analyst, Acme") == "analyst"
    assert tr._role_from_title("Acme Securities") == "unknown"
