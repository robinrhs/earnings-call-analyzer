"""The analyst brief the language model must return, as Pydantic models.

The schema mirrors what a sell-side or buy-side analyst writes down after a call:

* **Headline figures** - the reported numbers (revenue, margins, EPS, cash flow ...).
* **Guidance** - management's outlook, and whether it was raised, lowered or kept.
  Markets react to the *change* in guidance at least as much as to the quarter itself.
* **Tone** - a label plus a written justification. A bare sentiment score is not
  auditable; a justification backed by quotes is.
* **Risks** - what could go wrong, in management's own words.
* **Analyst questions** - what the analysts pushed on, and whether management gave a
  direct answer, a partial one, or deflected. Deflection on a key topic is a signal.

Every item carries an :class:`Evidence` with a verbatim quote and the paragraph id. The
model fills these in; :mod:`earnings_call_analyzer.verify` checks them afterwards.

All fields are required (``None`` is allowed where it makes sense) because the
structured-output API works best with fully specified schemas.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    # Reject keys the schema does not know, so silent schema drift shows up as an error.
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    paragraph_id: str = Field(description="Id of the paragraph containing the quote, e.g. P012.")
    quote: str = Field(
        description=(
            "Verbatim, contiguous excerpt copied character for character from that paragraph. "
            "One sentence or clause, at least eight words, no ellipses, no paraphrasing."
        )
    )


GuidanceDirection = Literal[
    "raised", "lowered", "maintained", "initiated", "withdrawn", "not_stated"
]
ToneLabel = Literal["confident", "constructive", "balanced", "cautious", "defensive"]
RiskCategory = Literal[
    "demand",
    "pricing_competition",
    "costs_supply_chain",
    "trade_regulation",
    "customer_concentration",
    "fx_macro",
    "execution",
    "liquidity_balance_sheet",
    "technology",
    "other",
]
AnswerQuality = Literal["direct", "partial", "deflected"]


class HeadlineFigure(_Strict):
    metric: str = Field(description="Name of the reported metric, e.g. 'Revenue'.")
    value: str = Field(description="Value with unit exactly as stated, e.g. '$412.3 million'.")
    period: str = Field(description="Reporting period, e.g. 'Q3 FY2026'.")
    comparison: str | None = Field(
        description="Comparison stated on the call, e.g. '+14% year over year', else null."
    )
    evidence: Evidence


class GuidanceItem(_Strict):
    metric: str = Field(description="Guided metric, e.g. 'Revenue' or 'Free cash flow'.")
    period: str = Field(description="Period the guidance covers, e.g. 'FY2026' or 'Q4 FY2026'.")
    current: str = Field(description="The guidance given on this call, as stated.")
    prior: str | None = Field(
        description="Previous guidance if management states it on this call, else null."
    )
    direction: GuidanceDirection = Field(
        description="How management describes the change versus prior guidance."
    )
    evidence: Evidence


class Tone(_Strict):
    label: ToneLabel
    justification: str = Field(
        description="Two to four sentences explaining the label, referring to the evidence."
    )
    evidence: list[Evidence] = Field(description="Two or three quotes that support the label.")


class Risk(_Strict):
    title: str = Field(description="Short title, e.g. 'Slower European orders'.")
    category: RiskCategory
    description: str = Field(description="One or two sentences on why it matters.")
    evidence: Evidence


class AnalystQuestion(_Strict):
    analyst: str
    firm: str | None
    topic: str = Field(description="Two to five words, e.g. 'Free cash flow guidance'.")
    question_summary: str
    answer_summary: str
    answer_quality: AnswerQuality = Field(
        description="direct = answered the question asked; partial = answered part of it; "
        "deflected = declined or answered a different question."
    )
    question_evidence: Evidence
    answer_evidence: Evidence


class Brief(_Strict):
    company: str
    period: str = Field(description="Quarter discussed on the call, e.g. 'Q3 FY2026'.")
    headline_figures: list[HeadlineFigure]
    guidance: list[GuidanceItem]
    tone: Tone
    risks: list[Risk]
    analyst_questions: list[AnalystQuestion]
