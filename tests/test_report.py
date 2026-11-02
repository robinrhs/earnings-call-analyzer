from pathlib import Path

import pytest

from earnings_call_analyzer.compare import compare_briefs
from earnings_call_analyzer.llm import load_replay
from earnings_call_analyzer.report import (
    ReportMeta,
    check_badge,
    render_brief_html,
    render_brief_markdown,
    render_comparison_html,
    render_comparison_markdown,
)
from earnings_call_analyzer.transcript import load_transcript
from earnings_call_analyzer.verify import QuoteCheck, verify_brief

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
META = ReportMeta(source="call.txt", model="fixture", generated_by="replayed", note="test note")


@pytest.fixture(scope="module")
def q3():
    transcript = load_transcript(EXAMPLES / "fernhill_q3_fy2026.txt")
    brief = load_replay(EXAMPLES / "replay" / "fernhill_q3_fy2026.json").brief
    return transcript, verify_brief(brief, transcript)


def test_badges():
    base = dict(cited_id="P001", matched_text=None, note="", score=1.0)
    assert (
        check_badge(QuoteCheck(found_id="P001", status="verified", method="exact", **base))
        == "VERIFIED"
    )
    corrected = QuoteCheck(found_id="P002", status="corrected", method="exact", **base)
    assert check_badge(corrected) == "CORRECTED P001->P002"
    fuzzy = QuoteCheck(
        found_id="P001", status="verified", method="fuzzy", **{**base, "score": 0.934}
    )
    assert check_badge(fuzzy) == "VERIFIED (fuzzy 0.93)"


def test_markdown_brief_shows_badges_quotes_and_rejections(q3):
    _, verified = q3
    md = render_brief_markdown(verified, META)
    assert md.startswith("# Earnings call brief: Fernhill Automation Inc. (fictional), Q3 FY2026")
    assert "`CORRECTED P017->P016`" in md
    assert "`VERIFIED (normalized)`" in md
    assert "`VERIFIED (fuzzy 0.98)`" in md
    assert "## Rejected by verification" in md
    assert "Net revenue retention (software): 118%" in md
    assert "not investment advice" in md
    assert "- Note: test note" in md


def test_html_brief_is_self_contained_and_links_to_paragraphs(q3):
    transcript, verified = q3
    html = render_brief_html(verified, transcript, META)
    assert html.lstrip().startswith("<!doctype html>")
    assert '<a href="#P016">P016</a>' in html
    assert '<p id="P016" class="cited">' in html
    assert 'class="badge corrected"' in html
    assert "<script" not in html and "http://" not in html  # no external resources


def test_html_escapes_text_from_the_transcript_and_model(q3):
    transcript, verified = q3
    evil = verified.model_copy(deep=True)
    evil.headline_figures[0].item.metric = "<script>alert(1)</script>"
    html = render_brief_html(evil, transcript, META)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_comparison_reports(q3):
    _, current = q3
    t2 = load_transcript(EXAMPLES / "fernhill_q2_fy2026.txt")
    previous = verify_brief(load_replay(EXAMPLES / "replay" / "fernhill_q2_fy2026.json").brief, t2)
    comparison = compare_briefs(previous, current)
    md = render_comparison_markdown(comparison, META, META)
    assert (
        "| Revenue | FY2026 | $1.58 billion to $1.63 billion | $1.62 billion to $1.66 billion "
        "| **raised** |" in md
    )
    assert "1 notch more positive: balanced -> constructive." in md
    html = render_comparison_html(comparison, META, META)
    assert '<span class="change-lowered">lowered</span>' in html
    assert "matched on category only" in md
    assert "## Not compared" not in md  # the clean example has nothing left out


def test_comparison_reports_list_items_that_were_not_compared(q3):
    _, current = q3
    t2 = load_transcript(EXAMPLES / "fernhill_q2_fy2026.txt")
    previous = verify_brief(load_replay(EXAMPLES / "replay" / "fernhill_q2_fy2026.json").brief, t2)
    comparison = compare_briefs(previous, current).model_copy(
        update={"warnings": ["Q3 FY2026: guidance 'Revenue (FY2026)' not compared: <test>"]}
    )
    md = render_comparison_markdown(comparison, META, META)
    assert "## Not compared\n\n- Q3 FY2026: guidance 'Revenue (FY2026)' not compared" in md
    html = render_comparison_html(comparison, META, META)
    assert "<h2>Not compared</h2>" in html and "&lt;test&gt;" in html
