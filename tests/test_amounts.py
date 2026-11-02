import pytest

from earnings_call_analyzer.amounts import (
    find_amounts,
    same_amount,
    share_range_units,
    signed_numbers,
)


def parsed(text):
    return [(a.value, a.unit, a.currency) for a in find_amounts(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1.62 billion", [(1.62, "billion", "$")]),
        ("$1,210.5m", [(1210.5, "million", "$")]),
        ("-4% to -2%", [(-4.0, "%", ""), (-2.0, "%", "")]),
        ("(3)% organic", [(-3.0, "%", "")]),
        ("($20.5)", [(-20.5, "", "$")]),
        ("-$20 million", [(-20.0, "million", "$")]),
        ("4%-6%", [(4.0, "%", ""), (6.0, "%", "")]),  # a range, not minus six
        ("$1.62-$1.66bn", [(1.62, "", "$"), (1.66, "billion", "$")]),
        ("margin up 50 bps", [(50.0, "bps", "")]),
        ("net leverage of 2.5x", [(2.5, "x", "")]),
        ("book-to-bill of 1.05", [(1.05, "", "")]),
    ],
)
def test_find_amounts(text, expected):
    assert parsed(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Q4", "FY2026", "fiscal 2026", "H2 FY26", "2Q26", "the 3rd quarter", "in 2025"],
)
def test_period_labels_and_years_are_not_amounts(text):
    assert find_amounts(text) == []


def test_a_year_with_currency_or_unit_is_an_amount():
    assert signed_numbers("$2026 and 2026 million") == [2026.0, 2026.0]


def test_typographic_minus_is_a_minus():
    assert signed_numbers("growth of \u22123%") == [-3.0]


def test_in_millions():
    billion, thousand, percent = find_amounts("$1.66 billion, 500 thousand, 15%")
    assert billion.in_millions() == 1660.0
    assert thousand.in_millions() == 0.5
    assert percent.in_millions() is None


def test_share_range_units_gives_the_first_end_the_unit_of_the_second():
    text = "$1.62 to $1.66 billion, then 4%-6%, and 3 to 5 bps"
    shared = share_range_units(text, find_amounts(text))
    assert [(a.value, a.unit, a.currency) for a in shared] == [
        (1.62, "billion", "$"),
        (1.66, "billion", "$"),
        (4.0, "%", ""),
        (6.0, "%", ""),
        (3.0, "bps", ""),
        (5.0, "bps", ""),
    ]


def test_share_range_units_leaves_separate_figures_alone():
    text = "revenue of $1.6 billion, margin 15"
    shared = share_range_units(text, find_amounts(text))
    assert [(a.value, a.unit) for a in shared] == [(1.6, "billion"), (15.0, "")]


@pytest.mark.parametrize(
    ("claimed", "source", "expected"),
    [
        ("$1,620 million", "$1.62 billion", True),  # 1,620 m = 1.62 bn
        ("$1.62 million", "$1.62 billion", False),  # same digits, 1,000x apart
        ("$1.62bn", "$1.62 billion", True),
        ("1.62", "$1.62 billion", True),  # no unit claimed: nothing to contradict
        ("$412.3 million", "412.3", True),  # source gives no unit
        ("15%", "15 bps", False),
        ("0.5 pp", "0.5 percentage points", True),
        ("\u20ac20 million", "$20 million", False),  # different currency
        ("750 thousand", "$0.75 million", True),  # 0.75 m either way
    ],
)
def test_same_amount(claimed, source, expected):
    (a,) = find_amounts(claimed)
    (b,) = find_amounts(source)
    assert same_amount(a, b) is expected
