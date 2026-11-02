"""Glue: transcript -> model (or replay) -> verification -> reports.

Kept separate from the CLI so the whole flow can be tested without parsing arguments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compare import Comparison, compare_briefs
from .llm import Effort, LLMResult, call_claude, load_replay, resolve_model, save_recording
from .pricing import cost_usd
from .report import (
    Origin,
    ReportMeta,
    render_brief_html,
    render_brief_markdown,
    render_comparison_html,
    render_comparison_markdown,
)
from .transcript import Transcript, load_transcript
from .verify import DEFAULT_SETTINGS, Policy, VerificationSettings, VerifiedBrief, verify_brief

# An earnings call has at least an introduction, some remarks and a closing. Fewer
# paragraphs than this means the file is empty, a scanned PDF without a text layer, or
# not a transcript, and sending it to the API would only cost money.
MIN_PARAGRAPHS = 3


def load_for_analysis(path: str | Path) -> Transcript:
    """Load a transcript and refuse one that has too little text to analyse."""
    transcript = load_transcript(path)
    count = len(transcript.paragraphs)
    if count < MIN_PARAGRAPHS:
        raise ValueError(
            f"{Path(path).name}: found {count} paragraph(s), need at least {MIN_PARAGRAPHS}. "
            "Is the file empty, or a scanned PDF without a text layer? "
            "Run the 'paragraphs' command to see what was parsed."
        )
    return transcript


@dataclass(frozen=True)
class AnalysisResult:
    transcript: Transcript
    llm: LLMResult
    verified: VerifiedBrief
    meta: ReportMeta


def _describe_source(llm: LLMResult, replay: str | Path | None) -> str:
    if llm.source == "replay":
        name = Path(replay).name if replay else "file"
        return f"replayed from {name} (no API call)"
    if llm.usage is None:
        return "Anthropic API"
    usage = llm.usage
    if usage.attempts:
        # Server-side fallback: price every attempt at the model that ran it.
        costs = [cost_usd(a.model, a.input_tokens, a.output_tokens) for a in usage.attempts]
        cost = None if None in costs else sum(c for c in costs if c is not None)
    else:
        cost = cost_usd(llm.model, usage.input_tokens, usage.output_tokens)
    attempts_text = ""
    if len(usage.attempts) > 1:
        models = ", ".join(a.model for a in usage.attempts)
        attempts_text = f" over {len(usage.attempts)} attempts ({models})"
    cost_text = f", about ${cost:.2f} at list prices" if cost is not None else ""
    return (
        f"Anthropic API, {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens{attempts_text}{cost_text}"
    )


# Model id written into hand-authored replay files (see examples/replay/).
FIXTURE_MODEL_PREFIX = "fixture"


def _origin(llm: LLMResult) -> Origin:
    """A replay whose model id starts with 'fixture' was written by hand, not by a model."""
    if llm.source == "replay" and llm.model.casefold().startswith(FIXTURE_MODEL_PREFIX):
        return "fixture"
    return "model"


def analyze_file(
    path: str | Path,
    *,
    replay: str | Path | None = None,
    record: str | Path | None = None,
    model: str | None = None,
    effort: Effort = "high",
    policy: Policy = "drop",
    settings: VerificationSettings = DEFAULT_SETTINGS,
    client: Any | None = None,
) -> AnalysisResult:
    transcript = load_for_analysis(path)
    if replay is not None:
        llm = load_replay(replay)
    else:
        llm, raw_text = call_claude(transcript, resolve_model(model), effort, client=client)
        if record is not None:
            save_recording(record, llm, raw_text)
    verified = verify_brief(llm.brief, transcript, policy=policy, settings=settings)
    # Only the file name goes into the report, never a local absolute path.
    meta = ReportMeta(
        source=Path(path).name,
        model=llm.model,
        generated_by=_describe_source(llm, replay),
        note=llm.note,
        origin=_origin(llm),
    )
    return AnalysisResult(transcript=transcript, llm=llm, verified=verified, meta=meta)


def write_brief_reports(
    result: AnalysisResult, out_dir: str | Path, formats: list[str], stem: str | None = None
) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or Path(result.meta.source).stem
    written = []
    if "md" in formats:
        path = out / f"{stem}.brief.md"
        path.write_text(render_brief_markdown(result.verified, result.meta), encoding="utf-8")
        written.append(path)
    if "html" in formats:
        path = out / f"{stem}.brief.html"
        html = render_brief_html(result.verified, result.transcript, result.meta)
        path.write_text(html, encoding="utf-8")
        written.append(path)
    if "json" in formats:
        path = out / f"{stem}.brief.json"
        path.write_text(result.verified.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def compare_results(previous: AnalysisResult, current: AnalysisResult) -> Comparison:
    return compare_briefs(previous.verified, current.verified)


def write_comparison_reports(
    comparison: Comparison,
    previous: AnalysisResult,
    current: AnalysisResult,
    out_dir: str | Path,
    formats: list[str],
) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(previous.meta.source).stem}_vs_{Path(current.meta.source).stem}"
    written = []
    if "md" in formats:
        path = out / f"{stem}.compare.md"
        md = render_comparison_markdown(comparison, previous.meta, current.meta)
        path.write_text(md, encoding="utf-8")
        written.append(path)
    if "html" in formats:
        path = out / f"{stem}.compare.html"
        html = render_comparison_html(comparison, previous.meta, current.meta)
        path.write_text(html, encoding="utf-8")
        written.append(path)
    if "json" in formats:
        path = out / f"{stem}.compare.json"
        path.write_text(json.dumps(comparison.model_dump(), indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
