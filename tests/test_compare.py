import pytest

from earnings_call_analyzer.compare import (
    compare_guidance,
    compare_risks,
    compare_tone,
    metric_key,
    midpoint,
    pair_figures,
    parse_amounts,
    period_key,
)
from earnings_call_analyzer.schema import Evidence, GuidanceItem, HeadlineFigure, Risk

EV = Evidence(paragraph_id="P001", quote="placeholder quote with enough words in it")


def g(metric, period, current, prior=None, direction="not_stated"):
    return GuidanceItem(
        metric=metric, period=period, current=current, prior=prior, direction=direction, evidence=EV
    )


def risk(title, category):
    return Risk(title=title, category=category, description="", evidence=EV)


def fig(metric, value):
    return HeadlineFigure(metric=metric, value=value, period="Q3", comparison=None, evidence=EV)


# --- parsing ----------------------------------------------------------------------------


def test_parse_amounts_scales_to_millions_and_inherits_range_unit():
    assert parse_amounts("$1.62 to $1.66 billion") == ([1620.0, 1660.0], "money")
    assert parse_amounts("$120 million to $135 million") == ([120.0, 135.0], "money")
    assert parse_amounts("approximately 15%") == ([15.0], "percent")
    assert parse_amounts("no numbers here") == ([], None)


def test_midpoint():
    assert midpoint("$1.58 billion to $1.63 billion") == (1605.0, "money")
    assert midpoint("$1,620 million to $1,660 million") == (1640.0, "money")


def test_period_and_metric_keys():
    assert period_key("FY2026") == period_key("fiscal year 2026") == "FY 2026"
    assert period_key("Q4 FY2026") == period_key("fourth quarter of fiscal 2026") == "Q4 2026"
    assert metric_key("Full-year revenue") == metric_key("Revenue")


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
