import pytest
from pydantic import ValidationError

from earnings_call_analyzer.schema import Brief

EVIDENCE = {"paragraph_id": "P001", "quote": "Revenue grew 14% year over year to $412.3 million"}


def minimal_brief() -> dict:
    return {
        "company": "Example Corp",
        "period": "Q3 FY2026",
        "headline_figures": [
            {
                "metric": "Revenue",
                "value": "$412.3 million",
                "period": "Q3 FY2026",
                "comparison": None,
                "evidence": EVIDENCE,
            }
        ],
        "guidance": [],
        "tone": {"label": "balanced", "justification": "Mixed.", "evidence": [EVIDENCE]},
        "risks": [],
        "analyst_questions": [],
    }


def test_valid_brief_parses():
    brief = Brief.model_validate(minimal_brief())
    assert brief.headline_figures[0].evidence.paragraph_id == "P001"


def test_unknown_tone_label_is_rejected():
    data = minimal_brief()
    data["tone"]["label"] = "euphoric"
    with pytest.raises(ValidationError):
        Brief.model_validate(data)


def test_unexpected_keys_are_rejected():
    data = minimal_brief()
    data["price_target"] = "$120"  # the brief must never invent a recommendation field
    with pytest.raises(ValidationError):
        Brief.model_validate(data)


def test_evidence_is_mandatory():
    data = minimal_brief()
    del data["headline_figures"][0]["evidence"]
    with pytest.raises(ValidationError):
        Brief.model_validate(data)


def test_guidance_direction_is_an_enum():
    data = minimal_brief()
    data["guidance"] = [
        {
            "metric": "Revenue",
            "period": "FY2026",
            "current": "$1.62bn to $1.66bn",
            "prior": "$1.58bn to $1.63bn",
            "direction": "up",
            "evidence": EVIDENCE,
        }
    ]
    with pytest.raises(ValidationError):
        Brief.model_validate(data)
    data["guidance"][0]["direction"] = "raised"
    assert Brief.model_validate(data).guidance[0].direction == "raised"
