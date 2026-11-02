import pytest

from earnings_call_analyzer.schema import (
    AnalystQuestion,
    Brief,
    Evidence,
    GuidanceItem,
    HeadlineFigure,
    Risk,
    Tone,
)
from earnings_call_analyzer.transcript import parse_transcript
from earnings_call_analyzer.verify import (
    VerificationSettings,
    check_quote,
    extract_numbers,
    normalize_text,
    verify_brief,
    worst_status,
)

CALL = """\
Prepared Remarks

Jane Doe -- Chief Executive Officer

Revenue grew 14% year over year to $412.3 million, ahead of the top end of our range.

Order intake from European customers slowed, and several customers pushed
final decisions into next year.

John Roe -- Chief Financial Officer

We are raising our fiscal 2026 revenue guidance to a range of $1.62 billion to $1.66 billion.

Question-and-Answer Session

Operator

Our first question comes from Sam Lee with Acme Securities.

Sam Lee -- Acme Securities

How confident are you that the inventory comes back out of the balance sheet next year?

John Roe -- Chief Financial Officer

The inventory is allocated to specific projects in our backlog that ship in the first half.
"""


@pytest.fixture
def transcript():
    return parse_transcript(CALL)


def ev(pid: str, quote: str) -> Evidence:
    return Evidence(paragraph_id=pid, quote=quote)


# --- single quotes ------------------------------------------------------------------------


def test_ids_in_fixture(transcript):
    assert [p.id for p in transcript.paragraphs] == [f"P00{i}" for i in range(1, 7)]
    assert transcript.get("P001").text.startswith("Revenue grew")


def test_exact_match(transcript):
    check = check_quote(ev("P001", "Revenue grew 14% year over year to $412.3 million"), transcript)
    assert (check.status, check.method, check.found_id) == ("verified", "exact", "P001")


def test_whitespace_case_and_typography_are_normalized(transcript):
    quote = "“Revenue  grew 14%  YEAR over year to $412.3 million — ahead”"
    check = check_quote(ev("P001", quote), transcript)
    assert (check.status, check.method) == ("verified", "normalized")
    assert check.matched_text == "Revenue grew 14% year over year to $412.3 million, ahead"


def test_dash_variants_normalize_to_same_text():
    assert normalize_text("year–over–year") == normalize_text("year-over-year")  # noqa: RUF001


def test_dollar_and_percent_signs_are_not_normalized_away(transcript):
    # "$412.3" vs "412.3" is a different claim; it must not pass the normalized step.
    check = check_quote(ev("P001", "grew 14 year over year to 412.3 million"), transcript)
    assert check.method != "normalized"


def test_fuzzy_match_accepts_one_changed_word(transcript):
    quote = "several customers pushed final decisions into the next year"
    check = check_quote(ev("P002", quote), transcript)
    assert (check.status, check.method, check.found_id) == ("verified", "fuzzy", "P002")
    assert check.score >= 0.90
    assert check.matched_text is not None


def test_fuzzy_rejects_a_changed_number_even_when_strings_are_similar(transcript):
    # Changing the guidance range is exactly the error we must catch: the strings are
    # more than 90% similar, but the numbers differ, so the quote is not accepted.
    quote = "raising our fiscal 2026 revenue guidance to a range of $1.70 billion to $1.66 billion"
    check = check_quote(ev("P003", quote), transcript)
    assert check.status == "unverified"
    assert "numbers differ" in check.note


def test_stricter_threshold_rejects_a_small_wording_slip(transcript):
    quote = "several customers pushed final decisions into the next year"
    strict = VerificationSettings(fuzzy_threshold=0.99)
    check = check_quote(ev("P002", quote), transcript, strict)
    assert check.status == "unverified"
    assert "Closest passage: P002" in check.note


def test_short_quotes_never_fuzzy_match(transcript):
    check = check_quote(ev("P002", "European customers slowed down"), transcript)
    assert check.status == "unverified"  # 4 words, not an exact match


def test_too_short_quote_is_rejected_even_if_present(transcript):
    check = check_quote(ev("P001", "Revenue grew 14%"), transcript)
    assert check.status == "unverified"
    assert "at least 5" in check.note


def test_wrong_paragraph_id_is_corrected(transcript):
    quote = "raising our fiscal 2026 revenue guidance to a range of $1.62 billion"
    check = check_quote(ev("P001", quote), transcript)
    assert (check.status, check.cited_id, check.found_id) == ("corrected", "P001", "P003")


def test_nonexistent_paragraph_id_is_corrected(transcript):
    quote = "raising our fiscal 2026 revenue guidance to a range of $1.62 billion"
    check = check_quote(ev("P999", quote), transcript)
    assert (check.status, check.found_id) == ("corrected", "P003")
    assert "does not exist" in check.note


def test_fuzzy_match_in_wrong_paragraph_is_corrected(transcript):
    quote = "several customers pushed their final decisions into next year"
    check = check_quote(ev("P006", quote), transcript)
    assert (check.status, check.method, check.found_id) == ("corrected", "fuzzy", "P002")


def test_fabricated_quote_is_unverified(transcript):
    quote = "We expect margins to expand by 300 basis points next year"
    check = check_quote(ev("P001", quote), transcript)
    assert (check.status, check.found_id, check.method) == ("unverified", None, "none")


def test_paragraph_id_lookup_is_forgiving_about_case_and_spaces(transcript):
    check = check_quote(
        ev(" p001 ", "Revenue grew 14% year over year to $412.3 million"), transcript
    )
    assert check.status == "verified"


def test_worst_status():
    assert worst_status([]) == "unverified"


def test_extract_numbers():
    assert extract_numbers("$1,210.5 million, up 14% from 0.9x") == [1210.5, 14.0, 0.9]


# --- whole brief ------------------------------------------------------------------------


def make_brief(**overrides) -> Brief:
    data = dict(
        company="Example Corp",
        period="Q3 FY2026",
        headline_figures=[
            HeadlineFigure(
                metric="Revenue",
                value="$412.3 million",
                period="Q3 FY2026",
                comparison="+14% YoY",
                evidence=ev("P001", "Revenue grew 14% year over year to $412.3 million"),
            ),
            HeadlineFigure(
                metric="EPS",
                value="$0.81",
                period="Q3 FY2026",
                comparison=None,
                evidence=ev("P001", "Adjusted earnings per share were $0.81, a record"),
            ),
        ],
        guidance=[
            GuidanceItem(
                metric="Revenue",
                period="FY2026",
                current="$1.62bn-$1.68bn",
                prior=None,
                direction="raised",
                evidence=ev("P003", "We are raising our fiscal 2026 revenue guidance"),
            )
        ],
        tone=Tone(
            label="constructive",
            justification="Growth ahead of range, Europe softer.",
            evidence=[
                ev("P001", "ahead of the top end of our range"),
                ev("P002", "Order intake from European customers slowed"),
            ],
        ),
        risks=[
            Risk(
                title="Europe",
                category="demand",
                description="Slower orders.",
                evidence=ev("P002", "several customers pushed final decisions into next year"),
            )
        ],
        analyst_questions=[
            AnalystQuestion(
                analyst="Sam Lee",
                firm="Acme Securities",
                topic="Inventory",
                question_summary="Will inventory unwind?",
                answer_summary="Allocated to backlog projects.",
                answer_quality="direct",
                question_evidence=ev(
                    "P005", "How confident are you that the inventory comes back out"
                ),
                answer_evidence=ev("P006", "The inventory is allocated to specific projects"),
            )
        ],
    )
    data.update(overrides)
    return Brief(**data)


def test_drop_policy_moves_unverified_items_to_rejected(transcript):
    result = verify_brief(make_brief(), transcript, policy="drop")
    assert [f.item.metric for f in result.headline_figures] == ["Revenue"]
    assert [(r.section, r.label) for r in result.rejected] == [("Headline figures", "EPS: $0.81")]
    assert result.counts()["unverified"] == 1


def test_flag_policy_keeps_unverified_items(transcript):
    result = verify_brief(make_brief(), transcript, policy="flag")
    statuses = [f.status for f in result.headline_figures]
    assert statuses == ["verified", "unverified"]
    assert result.rejected == []


def test_tone_is_kept_and_flagged_not_dropped(transcript):
    tone = Tone(
        label="confident", justification="x", evidence=[ev("P001", "made up words here ok")]
    )
    result = verify_brief(make_brief(tone=tone), transcript, policy="drop")
    assert result.tone.status == "unverified"


def test_number_in_guidance_not_in_source_paragraph_is_warned(transcript):
    result = verify_brief(make_brief(), transcript)
    warnings = result.guidance[0].warnings
    assert result.guidance[0].status == "verified"  # the quote itself is real ...
    assert any("1.68" in w for w in warnings)  # ... but 1.68 was never said


def test_analyst_question_speaker_checks(transcript):
    wrong = (
        make_brief()
        .analyst_questions[0]
        .model_copy(
            update={
                "analyst": "Pat Smith",
                "answer_evidence": ev(
                    "P005", "How confident are you that the inventory comes back"
                ),
            }
        )
    )
    result = verify_brief(make_brief(analyst_questions=[wrong]), transcript)
    warnings = result.analyst_questions[0].warnings
    assert any("answer quote is from Sam Lee (analyst)" in w for w in warnings)
    assert any("attributed to Pat Smith" in w for w in warnings)


def test_clean_question_has_no_warnings(transcript):
    result = verify_brief(make_brief(), transcript)
    assert result.analyst_questions[0].warnings == []
    assert result.analyst_questions[0].status == "verified"
