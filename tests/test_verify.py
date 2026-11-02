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
    meaningful_differences,
    normalize_text,
    normalize_word,
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
    assert "changes the claim: '$1.70' vs '$1.62'" in check.note


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


def test_extract_numbers_keeps_signs_and_skips_periods():
    assert extract_numbers("organic growth of -3% (2)% in Q4 of fiscal 2026") == [-3.0, -2.0]


# --- adversarial cases: a quote that reads almost the same but claims something else ----

TRAPS = """Prepared Remarks

Jane Doe -- Chief Executive Officer

Software revenue grew 19% to $82 million in the quarter, and organic growth
in Europe was -3% as orders slowed.

We now expect full-year revenue of $1.62 billion to $1.66 billion, and we do
not expect further margin pressure next year.

Segment EBITDA was (4)% of sales in the start-up plants, and revenue was up 2.5 million units.
"""


@pytest.fixture
def traps():
    return parse_transcript(TRAPS)


@pytest.mark.parametrize(
    ("pid", "quote", "why"),
    [
        ("P001", "9% to $82 million in the quarter", "starts inside '19%'"),
        ("P001", "organic growth in Europe was 3% as orders slowed", "drops the minus sign"),
        (
            "P002",
            "We now expect full-year revenue of $1.62 billion to $1.6",
            "ends inside '$1.66'",
        ),
        ("P003", "Segment EBITDA was 4% of sales in the start-up plants", "drops the brackets"),
        ("P003", "and revenue was up 5 million units", "starts inside '2.5'"),
        (
            "P002",
            "We now expect full-year revenue of $1.70 billion to $1.66 billion",
            "1.62 became 1.70",
        ),
        (
            "P002",
            "We now expect full-year revenue of $1.62 million to $1.66 million",
            "billion became million",
        ),
        ("P002", "and we do expect further margin pressure next year", "drops 'not'"),
        (
            "P002",
            "We now expect full-year revenue of $1.62 billion to $1.66 billion, and we do "
            "not expect lower margin pressure",
            "'further' became 'lower'",
        ),
    ],
)
def test_quotes_that_change_the_claim_are_rejected(traps, pid, quote, why):
    check = check_quote(ev(pid, quote), traps)
    assert check.status == "unverified", why


@pytest.mark.parametrize(
    ("quote", "method"),
    [
        ("Software revenue grew 19% to $82 million in the quarter", "exact"),
        ("organic growth in Europe was -3% as orders slowed", "exact"),
        ("organic growth in Europe was \u22123% as orders slowed", "normalized"),
        ("Segment EBITDA was -4% of sales in the start-up plants", "normalized"),
        ("Software revenue grew 19% to $82 million in this quarter", "fuzzy"),
    ],
)
def test_signed_and_near_verbatim_quotes_still_pass(traps, quote, method):
    pid = "P003" if "EBITDA" in quote else "P001"
    check = check_quote(ev(pid, quote), traps)
    assert (check.status, check.method) == ("verified", method)


def test_a_harmless_truncated_word_is_only_a_fuzzy_match(traps):
    quote = "Software revenue grew 19% to $82 million in the q"
    check = check_quote(ev("P001", quote), traps)
    assert (check.status, check.method) == ("verified", "fuzzy")


def test_rejected_near_match_names_the_difference(traps):
    quote = "We now expect full-year revenue of $1.62 million to $1.66 million"
    check = check_quote(ev("P002", quote), traps)
    assert "'million' vs 'billion'" in check.note


def test_normalize_word_keeps_the_sign_of_a_number():
    assert normalize_word("-3%") == "-3%"
    assert normalize_word("(3)%") == "-3%"
    assert normalize_word("($20.5),") == "-$20.5"
    assert normalize_word("(14%") == "14%"  # an opening bracket alone is just punctuation
    assert normalize_word("-revenue") == "revenue"
    assert normalize_word("\u2014") == ""


def test_meaningful_differences_ignores_small_words():
    assert meaningful_differences("that projects are", "that the projects are") == []
    assert meaningful_differences("we do expect", "we do not expect") == ["'(nothing)' vs 'not'"]
    assert meaningful_differences("revenue was up", "revenue was down") == ["'up' vs 'down'"]


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


def test_number_warning_understands_scale_and_sign():
    t = parse_transcript(
        "Prepared Remarks\n\nJane Doe -- CEO\n\n"
        "Revenue was $1.62 billion, a decline of 3% from last year.\n"
    )
    figure = HeadlineFigure(
        metric="Revenue",
        value="$1,620 million, -3% for fiscal 2026",
        period="FY2026",
        comparison=None,
        evidence=ev("P001", "Revenue was $1.62 billion, a decline of 3% from last year"),
    )
    result = verify_brief(make_brief(headline_figures=[figure], guidance=[]), t, policy="flag")
    assert result.headline_figures[0].warnings == []


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
