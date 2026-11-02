from pathlib import Path

import pytest

from earnings_call_analyzer.compare import (
    _compare_values,
    compare_briefs,
    compare_risks,
    compare_tone,
    metric_key,
    midpoint,
    pair_figures,
    parse_guidance,
    period_key,
)
from earnings_call_analyzer.compare import compare_guidance as _compare_guidance
from earnings_call_analyzer.llm import load_replay
from earnings_call_analyzer.schema import Evidence, GuidanceItem, HeadlineFigure, Risk
from earnings_call_analyzer.transcript import load_transcript
from earnings_call_analyzer.verify import verify_brief

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

EV = Evidence(paragraph_id="P001", quote="placeholder quote with enough words in it")


def g(metric, period, current, prior=None, direction="not_stated"):
    return GuidanceItem(
        metric=metric, period=period, current=current, prior=prior, direction=direction, evidence=EV
    )


def risk(title, category):
    return Risk(title=title, category=category, description="", evidence=EV)


def fig(metric, value):
    return HeadlineFigure(metric=metric, value=value, period="Q3", comparison=None, evidence=EV)


def compare_guidance(*args, **kwargs):
    changes, _warnings = _compare_guidance(*args, **kwargs)
    return changes


# --- parsing ----------------------------------------------------------------------------


def span(text, metric=""):
    guided = parse_guidance(text, metric)
    return None if guided is None else (guided.low, guided.high, guided.kind)


def test_parse_guidance_scales_to_millions_and_inherits_range_unit():
    assert span("$1.62 to $1.66 billion") == (1620.0, 1660.0, "millions")
    assert span("$120 million to $135 million") == (120.0, 135.0, "millions")
    assert span("approximately 15%") == (15.0, 15.0, "percent")
    assert span("no numbers here") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Period labels and years inside the string are not part of the range.
        ("$1.60 billion to $1.65 billion for fiscal 2026", (1600.0, 1650.0, "millions")),
        ("$435 million to $450 million in Q4", (435.0, 450.0, "millions")),
        ("for FY2026, $1.6 billion to $1.7 billion", (1600.0, 1700.0, "millions")),
        # Signs: a minus, accounting brackets, or loss/decline wording.
        ("-4% to -2%", (-4.0, -2.0, "percent")),
        ("(3)% to (1)%", (-3.0, -1.0, "percent")),
        ("an operating loss of $20 million to $10 million", (-20.0, -10.0, "millions")),
        ("a decline of 2% to 4%", (-4.0, -2.0, "percent")),
        # Only the first range counts; "up 8% to 10%" is a second figure.
        ("$1.6 billion, up 8% to 10%", (1600.0, 1600.0, "millions")),
        # Per-share and other non-money amounts keep their own kind.
        ("EPS of $2.10 to $2.20", (2.10, 2.20, "currency")),
        ("margin expansion of 50 to 100 bps", (50.0, 100.0, "bps")),
        ("net leverage below 2.5x", (2.5, 2.5, "multiple")),
        ("three new plants in 2 regions", None),
    ],
)
def test_parse_guidance_edge_cases(text, expected):
    assert span(text) == expected


def test_loss_in_the_metric_name_makes_the_range_negative():
    assert span("$20 million to $10 million", "Operating loss") == (-20.0, -10.0, "millions")


@pytest.mark.parametrize(
    ("text", "metric", "expected"),
    [
        # A sign word elsewhere in the sentence does not govern the range.
        (
            "Despite the decline in European orders, revenue guidance of "
            "$1.62 billion to $1.66 billion",
            "Revenue",
            (1620.0, 1660.0, "millions"),
        ),
        (
            "After paying down debt, revenue of $1.62 billion to $1.66 billion",
            "Revenue",
            (1620.0, 1660.0, "millions"),
        ),
        (
            "We paid down debt and expect revenue of $1.62 billion to $1.66 billion",
            "Revenue",
            (1620.0, 1660.0, "millions"),
        ),
        # A loss ratio is a positive percentage, in the metric name or in the text.
        ("64% to 66%", "Combined loss ratio", (64.0, 66.0, "percent")),
        ("a loss ratio of 64% to 66%", "Combined ratio", (64.0, 66.0, "percent")),
        # Sign words that do govern the range, up to five words before it.
        ("a net loss in the range of $20 million to $10 million", "", (-20.0, -10.0, "millions")),
        ("we expect revenue to be down 2% to 4%", "Revenue", (-4.0, -2.0, "percent")),
    ],
)
def test_sign_words_only_count_next_to_the_range(text, metric, expected):
    assert span(text, metric) == expected


def test_context_sign_words_do_not_flip_a_raise_into_a_cut():
    # By hand: midpoint (1,580 + 1,630) / 2 = 1,605 -> (1,620 + 1,660) / 2 = 1,640,
    # +35 million, 35 / 1,605 = +2.18%.
    current = (
        "Despite the decline in European orders, revenue guidance of $1.62 billion to $1.66 billion"
    )
    assert _compare_values("$1.58 billion to $1.63 billion", current, "Revenue") == (
        "raised",
        "Midpoint 1,605 -> 1,640 million (+35 million, +2.2%).",
    )
    # A higher loss ratio: 65% -> 67%, +2 pp, not "-65% -> -67%".
    assert _compare_values("64% to 66%", "66% to 68%", "Combined loss ratio") == (
        "raised",
        "Midpoint 65% -> 67% (+2 pp).",
    )


def test_midpoint():
    assert midpoint("$1.58 billion to $1.63 billion") == (1605.0, "millions")
    assert midpoint("$1,620 million to $1,660 million") == (1640.0, "millions")


@pytest.mark.parametrize(
    ("previous", "current", "kind", "detail"),
    [
        ("-4% to -2%", "-1% to 1%", "raised", "Midpoint -3% -> 0% (+3 pp)."),
        (
            "an operating loss of $20 million to $10 million",
            "an operating loss of $40 million to $30 million",
            "lowered",
            "Midpoint -15 -> -35 million (-20 million).",
        ),
        ("flat, about 0%", "growth of 4%", "raised", "Midpoint 0% -> 4% (+4 pp)."),
        ("about 0%", "about 0%", "maintained", "Midpoint unchanged."),
        (
            "$1.6 billion, up 8% to 10%",
            "$1.7 billion, up 8% to 10%",
            "raised",
            "Midpoint 1,600 -> 1,700 million (+100 million, +6.2%).",
        ),
        (
            "EPS of $2.10 to $2.20",
            "EPS of $2.30 to $2.40",
            "raised",
            "Midpoint $2.15 -> $2.35 (+$0.20, +9.3%).",
        ),
        (
            "$1.60 billion to $1.65 billion for fiscal 2026",
            "$1.62 billion to $1.66 billion for fiscal 2026",
            "raised",
            "Midpoint 1,625 -> 1,640 million (+15 million, +0.9%).",
        ),
        ("$1.6 billion", "15%", "changed", "Not comparable as numbers."),
    ],
)
def test_compare_values(previous, current, kind, detail):
    assert _compare_values(previous, current) == (kind, detail)


def test_other_figures_in_the_wording_are_flagged():
    kind, detail = _compare_values("$1.6 billion, up 8%", "$1.6 billion, up 4%")
    assert kind == "maintained"
    assert "Other figures in the wording differ" in detail


def test_period_and_metric_keys():
    assert period_key("FY2026") == period_key("fiscal year 2026") == "FY 2026"
    assert period_key("Q4 FY2026") == period_key("fourth quarter of fiscal 2026") == "Q4 2026"
    assert period_key("H2 FY2026") == period_key("second half of fiscal 2026") == "H2 2026"
    assert period_key("first half 2026") == "H1 2026"
    assert period_key("calendar 2026") == period_key("CY2026") == "CY 2026"
    assert metric_key("Full-year revenue") == metric_key("Revenue")


def test_half_year_guidance_does_not_overwrite_full_year():
    previous = [g("Revenue", "FY2026", "$1.6 billion"), g("Revenue", "H2 FY2026", "$0.8 billion")]
    current = [g("Revenue", "FY2026", "$1.6 billion"), g("Revenue", "H2 FY2026", "$0.9 billion")]
    changes = {c.period: c.change for c in compare_guidance(previous, current, "Q2", [])}
    assert changes == {"FY2026": "maintained", "H2 FY2026": "raised"}


def test_duplicate_guidance_key_is_warned_not_overwritten():
    current = [g("Revenue", "FY2026", "$1.6 billion"), g("Revenue", "fiscal 2026", "$1.7 billion")]
    changes, warnings = _compare_guidance([], current, "Q3", [], previous_period="Q2")
    assert [c.current for c in changes] == ["$1.6 billion"]
    assert len(warnings) == 1 and "Only the first is compared" in warnings[0]


# --- guidance ---------------------------------------------------------------------------


def test_guidance_raised_lowered_maintained():
    previous = [
        g("Revenue", "FY2026", "$1.58 billion to $1.63 billion"),
        g("Free cash flow", "FY2026", "$140 million to $155 million"),
        g("Adjusted operating margin", "FY2026", "approximately 15%"),
    ]
    current = [
        g("Revenue", "FY2026", "$1.62 billion to $1.66 billion", direction="raised"),
        g("Free cash flow", "FY2026", "$120 million to $135 million", direction="lowered"),
        g("Adjusted operating margin", "FY2026", "approximately 15%", direction="maintained"),
    ]
    changes = {c.metric: c for c in compare_guidance(previous, current, "Q3 FY2026", [])}
    assert changes["Revenue"].change == "raised"
    assert "+2.2%" in changes["Revenue"].detail
    assert changes["Free cash flow"].change == "lowered"
    assert changes["Adjusted operating margin"].change == "maintained"


def test_same_range_in_different_units_is_maintained():
    previous = [g("Revenue", "FY2026", "$1.6 billion to $1.7 billion")]
    current = [g("Revenue", "FY2026", "$1,600 million to $1,700 million")]
    assert compare_guidance(previous, current, "Q3", [])[0].change == "maintained"


def test_new_guidance_uses_prior_stated_on_the_call():
    current = [g("Revenue", "FY2026", "$1.62 billion to $1.66 billion", prior="$1.58bn to $1.63bn")]
    change = compare_guidance([], current, "Q3 FY2026", [])[0]
    assert change.change == "raised"
    assert "stated on this call" in change.detail


def test_new_and_dropped_guidance():
    previous = [g("Capex", "FY2026", "$40 million")]
    current = [g("Revenue", "Q4 FY2026", "$435 million to $450 million", direction="initiated")]
    kinds = {c.metric: c.change for c in compare_guidance(previous, current, "Q3 FY2026", [])}
    assert kinds == {"Revenue": "new", "Capex": "dropped"}


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("$412.3 million", "within the guided range (upper half)"),
        ("$401 million", "within the guided range (lower half)"),
        ("$420 million", "above the guided range"),
        ("$390 million", "below the guided range"),
    ],
)
def test_last_quarters_quarterly_guide_is_compared_with_the_actual(actual, expected):
    previous = [g("Revenue", "Q3 FY2026", "$400 million to $415 million")]
    change = compare_guidance(previous, [], "Q3 FY2026", [fig("Revenue", actual)])[0]
    assert change.change == "reported"
    assert expected in change.detail


def test_stated_direction_mismatch_is_noted():
    previous = [g("Revenue", "FY2026", "$1.6 billion")]
    current = [g("Revenue", "FY2026", "$1.5 billion", direction="maintained")]
    change = compare_guidance(previous, current, "Q3", [])[0]
    assert change.change == "lowered"
    assert "described it as maintained" in change.detail


# --- risks, tone, figures ---------------------------------------------------------------


def test_risks_new_persisting_dropped():
    previous = [
        risk("Component lead times", "costs_supply_chain"),
        risk("Wage inflation", "execution"),
    ]
    current = [risk("Inventory build", "costs_supply_chain"), risk("Europe slowdown", "demand")]
    result = {(r.change, r.category) for r in compare_risks(previous, current)}
    assert result == {
        ("persisting", "costs_supply_chain"),
        ("new", "demand"),
        ("dropped", "execution"),
    }


def test_category_only_risk_pairings_are_labelled():
    previous = [risk("Component lead times", "costs_supply_chain"), risk("Wage costs", "execution")]
    current = [
        risk("Inventory build", "costs_supply_chain"),
        risk("Wage costs rising", "execution"),
    ]
    matched = {r.category: r.matched_on for r in compare_risks(previous, current)}
    assert matched == {"costs_supply_chain": "category", "execution": "title"}


def test_other_category_needs_similar_titles():
    previous = [risk("Litigation over patents", "other")]
    current = [risk("Patents litigation risk", "other"), risk("CEO succession", "other")]
    result = sorted(
        (r.change, r.current_title or r.previous_title) for r in compare_risks(previous, current)
    )
    assert result == [("new", "CEO succession"), ("persisting", "Patents litigation risk")]


def test_tone_shift():
    assert compare_tone("balanced", "constructive").steps == 1
    assert compare_tone("confident", "cautious").steps == -3
    assert "more negative" in compare_tone("confident", "cautious").description
    assert compare_tone("balanced", "balanced").description == "Unchanged (balanced)."


def test_pair_figures():
    pairs = pair_figures([fig("Revenue", "$1"), fig("EBITDA", "$2")], [fig("Revenue", "$3")])
    assert [(p.metric, p.previous, p.current) for p in pairs] == [
        ("Revenue", "$1", "$3"),
        ("EBITDA", "$2", None),
    ]


# --- whole briefs: only trustworthy items are compared ----------------------------------


@pytest.fixture(scope="module")
def fernhill():
    briefs = {}
    for quarter in ("q2", "q3"):
        transcript = load_transcript(EXAMPLES / f"fernhill_{quarter}_fy2026.txt")
        brief = load_replay(EXAMPLES / "replay" / f"fernhill_{quarter}_fy2026.json").brief
        briefs[quarter] = (brief, transcript)
    return briefs


def test_guidance_with_a_number_warning_is_not_compared(fernhill):
    (q2, t2), (q3, t3) = fernhill["q2"], fernhill["q3"]
    # A real quote, but the model typed a range that is not in the paragraph.
    wrong = q3.model_copy(deep=True)
    wrong.guidance[0].current = "$1.70 billion to $1.74 billion"
    current = verify_brief(wrong, t3)
    assert current.guidance[0].status == "verified" and current.guidance[0].warnings
    comparison = compare_briefs(verify_brief(q2, t2), current)
    revenue_fy = [c for c in comparison.guidance if (c.metric, c.period) == ("Revenue", "FY2026")]
    assert [c.change for c in revenue_fy] == ["not_compared"]  # not "raised", not "dropped"
    assert "Q3 FY2026 item failed the checks" in revenue_fy[0].detail
    assert any("not compared" in w and "1.7" in w for w in comparison.warnings)


def test_fabricated_prior_range_does_not_decide_the_direction(fernhill):
    (q2, t2), (q3, t3) = fernhill["q2"], fernhill["q3"]
    # Without revenue guidance in last quarter's brief, the prior range stated on this
    # call is the only basis for raised/lowered. The transcript says $1.58bn-$1.63bn
    # (midpoint 1,605 -> 1,640: raised); a made-up $1.70bn-$1.75bn would read as lowered.
    previous = q2.model_copy(deep=True)
    previous.guidance = [x for x in previous.guidance if x.metric != "Revenue"]
    fabricated = q3.model_copy(deep=True)
    fabricated.guidance[0].prior = "$1.70 billion to $1.75 billion"
    current = verify_brief(fabricated, t3)
    assert current.guidance[0].status == "verified"
    assert (
        current.guidance[0]
        .warnings[0]
        .startswith("Prior guidance contains $1.7 billion, $1.75 billion")
    )
    comparison = compare_briefs(verify_brief(previous, t2), current)
    revenue_fy = [c for c in comparison.guidance if (c.metric, c.period) == ("Revenue", "FY2026")]
    assert revenue_fy == []  # withheld, so no direction at all
    assert any("Revenue (FY2026)' not compared: Prior guidance" in w for w in comparison.warnings)

    # The real prior range from the transcript gives the hand-computed answer.
    real = compare_briefs(verify_brief(previous, t2), verify_brief(q3, t3))
    (change,) = [c for c in real.guidance if (c.metric, c.period) == ("Revenue", "FY2026")]
    assert change.change == "raised"
    assert change.detail.startswith("Midpoint 1,605 -> 1,640 million (+35 million, +2.2%).")


def test_unverified_tone_is_not_compared(fernhill):
    (q2, t2), (q3, t3) = fernhill["q2"], fernhill["q3"]
    fabricated = q3.model_copy(deep=True)
    fabricated.tone.label = "confident"
    fabricated.tone.evidence[0].quote = "We have never been more certain about the year ahead"
    current = verify_brief(fabricated, t3)
    assert current.tone.status == "unverified"
    tone = compare_briefs(verify_brief(q2, t2), current).tone
    assert tone.steps is None
    assert tone.description.startswith("Not compared")


def test_flagged_unverified_items_are_listed_as_not_compared(fernhill):
    (q2, t2), (q3, t3) = fernhill["q2"], fernhill["q3"]
    comparison = compare_briefs(
        verify_brief(q2, t2, policy="flag"), verify_brief(q3, t3, policy="flag")
    )
    assert any("Net revenue retention" in w and "unverified" in w for w in comparison.warnings)
    assert all(f.metric != "Net revenue retention (software)" for f in comparison.figures)


def test_clean_example_comparison_has_no_warnings(fernhill):
    (q2, t2), (q3, t3) = fernhill["q2"], fernhill["q3"]
    comparison = compare_briefs(verify_brief(q2, t2), verify_brief(q3, t3))
    assert comparison.warnings == []
    assert comparison.tone.steps == 1
