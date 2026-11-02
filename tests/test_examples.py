"""Guards the claims the README makes about the example run."""

from pathlib import Path

from earnings_call_analyzer.pipeline import analyze_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_q3_fixture_errors_are_caught_as_documented():
    result = analyze_file(
        EXAMPLES / "fernhill_q3_fy2026.txt",
        replay=EXAMPLES / "replay" / "fernhill_q3_fy2026.json",
    )
    checks = result.verified.all_checks()
    methods = [(c.status, c.method) for c in checks]
    assert methods.count(("corrected", "exact")) == 1  # wrong paragraph id
    assert methods.count(("verified", "normalized")) == 1  # dash instead of comma
    assert methods.count(("verified", "fuzzy")) == 1  # dropped word
    assert methods.count(("unverified", "none")) == 1  # fabricated figure
    assert [r.label for r in result.verified.rejected] == ["Net revenue retention (software): 118%"]


def test_committed_example_output_is_up_to_date(tmp_path):
    from earnings_call_analyzer.pipeline import write_brief_reports

    result = analyze_file(
        EXAMPLES / "fernhill_q3_fy2026.txt",
        replay=EXAMPLES / "replay" / "fernhill_q3_fy2026.json",
    )
    (fresh,) = write_brief_reports(result, tmp_path, ["md"])
    committed = EXAMPLES / "output" / "fernhill_q3_fy2026.brief.md"
    assert fresh.read_text() == committed.read_text()
