"""Render verified briefs and comparisons as Markdown and self-contained HTML.

The reports are built so a reader can audit every line: each item shows its
verification badge, the quote, and the paragraph id. In the HTML report the id links
to the paragraph in the transcript appendix at the bottom of the same file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jinja2 import Environment, PackageLoader, StrictUndefined

from .compare import Comparison
from .transcript import SECTION_LABELS, Transcript
from .verify import QuoteCheck, VerifiedBrief

# The first sentence says where the content came from; it must be literally true.
ORIGIN_SENTENCES = {
    "model": "Generated with a language model and checked automatically against the transcript.",
    "fixture": (
        "Built from a hand-authored fixture that stands in for a model response "
        "(no language model was called) and checked automatically against the transcript."
    ),
    "mixed": (
        "Built partly from a language model response and partly from a hand-authored "
        "fixture (see the sources below), and checked automatically against the transcript."
    ),
}
CAVEAT = (
    "Verification shows that a quote exists, not that the interpretation is right. "
    "This is a research aid, not investment advice."
)
Origin = Literal["model", "fixture"]


@dataclass(frozen=True)
class ReportMeta:
    source: str
    model: str
    generated_by: str  # "Anthropic API, ... tokens" or "replayed from file.json"
    note: str = ""  # e.g. the note stored in a replay file
    # "model": a real API response (live or a recording); "fixture": hand-authored.
    origin: Origin = "model"


def disclaimer(*metas: ReportMeta) -> str:
    """The banner at the top of a report, naming the true origin of its content."""
    origins = {meta.origin for meta in metas}
    key = origins.pop() if len(origins) == 1 else "mixed"
    return f"{ORIGIN_SENTENCES[key]} {CAVEAT}"


def check_badge(check: QuoteCheck) -> str:
    """Short text badge, e.g. 'VERIFIED', 'VERIFIED (fuzzy 0.95)', 'CORRECTED P013->P014'."""
    if check.status == "verified":
        if check.method == "fuzzy":
            return f"VERIFIED (fuzzy {check.score:.2f})"
        return "VERIFIED" if check.method == "exact" else "VERIFIED (normalized)"
    if check.status == "corrected":
        return f"CORRECTED {check.cited_id}->{check.found_id}"
    return "UNVERIFIED"


RISK_LABELS = {
    "demand": "demand",
    "pricing_competition": "pricing / competition",
    "costs_supply_chain": "costs / supply chain",
    "trade_regulation": "trade / regulation",
    "customer_concentration": "customer concentration",
    "fx_macro": "FX / macro",
    "execution": "execution",
    "liquidity_balance_sheet": "liquidity / balance sheet",
    "technology": "technology",
    "other": "other",
}


def shown_id(check: QuoteCheck) -> str:
    """The id a reader should look at: where the quote was found, else the cited id."""
    return check.found_id or check.cited_id


def _environment(autoescape: bool) -> Environment:
    env = Environment(
        loader=PackageLoader("earnings_call_analyzer", "templates"),
        # HTML output escapes everything from the transcript and the model (no injection);
        # Markdown output is plain text.
        autoescape=autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["badge"] = check_badge
    env.filters["shown_id"] = shown_id
    env.globals["section_labels"] = SECTION_LABELS
    env.globals["zip"] = zip
    env.filters["risk_label"] = lambda category: RISK_LABELS.get(category, category)
    return env


def _cited_ids(brief: VerifiedBrief) -> set[str]:
    return {shown_id(c) for c in brief.all_checks() if c.status != "unverified"}


def render_brief_markdown(brief: VerifiedBrief, meta: ReportMeta) -> str:
    template = _environment(autoescape=False).get_template("brief.md.j2")
    return template.render(
        brief=brief, meta=meta, counts=brief.counts(), disclaimer=disclaimer(meta)
    )


def render_brief_html(brief: VerifiedBrief, transcript: Transcript, meta: ReportMeta) -> str:
    template = _environment(autoescape=True).get_template("brief.html.j2")
    return template.render(
        brief=brief,
        meta=meta,
        counts=brief.counts(),
        disclaimer=disclaimer(meta),
        transcript=transcript,
        cited=_cited_ids(brief),
    )


def render_comparison_markdown(
    comparison: Comparison, previous: ReportMeta, current: ReportMeta
) -> str:
    template = _environment(autoescape=False).get_template("compare.md.j2")
    return template.render(
        c=comparison,
        previous=previous,
        current=current,
        disclaimer=disclaimer(previous, current),
    )


def render_comparison_html(
    comparison: Comparison, previous: ReportMeta, current: ReportMeta
) -> str:
    template = _environment(autoescape=True).get_template("compare.html.j2")
    return template.render(
        c=comparison,
        previous=previous,
        current=current,
        disclaimer=disclaimer(previous, current),
    )
