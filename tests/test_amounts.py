import pytest

from earnings_call_analyzer.amounts import find_amounts, signed_numbers


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
