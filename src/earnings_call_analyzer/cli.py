"""Command line interface.

python -m earnings_call_analyzer analyze call.txt
python -m earnings_call_analyzer analyze call.txt --replay recorded.json
python -m earnings_call_analyzer compare prev.txt curr.txt
python -m earnings_call_analyzer paragraphs call.txt     # check the parser first
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .llm import (
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    LLMError,
    MissingApiKeyError,
    brief_json_schema,
    resolve_model,
)
from .pipeline import (
    AnalysisResult,
    analyze_file,
    compare_results,
    write_brief_reports,
    write_comparison_reports,
)
from .pricing import OUTPUT_TOKENS_HIGH, OUTPUT_TOKENS_LOW, PRICES_AS_OF, estimate_run
from .prompt import SYSTEM_PROMPT, build_user_message
from .transcript import Transcript, load_transcript
from .verify import VerificationSettings

FORMATS = ("md", "html", "json")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        help=f"Claude model id (default: ${MODEL_ENV_VAR} or {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default="high",
        help="How much reasoning the model spends (default: high).",
    )
    parser.add_argument(
        "--unverified",
        choices=["drop", "flag"],
        default="drop",
        help="drop: remove items whose quote is not found (listed separately); "
        "flag: keep them with a red badge (default: drop).",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=VerificationSettings().fuzzy_threshold,
        help="Minimum similarity for a near-verbatim quote match (default: %(default)s).",
    )
    parser.add_argument(
        "--out-dir", default="reports", help="Where to write the reports (default: reports/)."
    )
    parser.add_argument(
        "--format",
        nargs="+",
        choices=FORMATS,
        default=list(FORMATS),
        help="Report formats to write (default: md html json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the transcript and print a cost estimate without calling the API.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earnings-call-analyzer",
        description="Turn earnings call transcripts into analyst briefs with verified quotes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyse one transcript (.txt or .pdf).")
    analyze.add_argument("transcript", type=Path)
    analyze.add_argument("--replay", type=Path, help="Use a recorded model response (JSON).")
    analyze.add_argument("--record", type=Path, help="Save the model response for replay.")
    _add_common(analyze)

    compare = sub.add_parser("compare", help="Compare two calls quarter over quarter.")
    compare.add_argument("previous", type=Path)
    compare.add_argument("current", type=Path)
    compare.add_argument(
        "--replay",
        nargs=2,
        type=Path,
        metavar=("PREV_JSON", "CURR_JSON"),
        help="Recorded model responses for the two calls.",
    )
    compare.add_argument(
        "--record",
        nargs=2,
        type=Path,
        metavar=("PREV_JSON", "CURR_JSON"),
        help="Save both model responses for replay.",
    )
    _add_common(compare)

    paragraphs = sub.add_parser("paragraphs", help="Show how a transcript is parsed.")
    paragraphs.add_argument("transcript", type=Path)
    return parser


# --- Output helpers ---------------------------------------------------------------------


def _parse_summary(transcript: Transcript, name: str) -> str:
    sections = Counter(p.section for p in transcript.paragraphs)
    return (
        f"Parsed {len(transcript.paragraphs)} paragraphs from {name}: "
        f"{sections['prepared_remarks']} prepared remarks, {sections['qa']} Q&A, "
        f"{sections['front_matter']} front matter."
    )


def _usd(amount: float) -> str:
    return f"${amount:.2f}" if amount >= 0.1 else f"${amount:.3f}"


def _print_estimate(transcript: Transcript, model: str) -> None:
    # The JSON schema is sent with the request as well, so it counts towards the input.
    prompt = SYSTEM_PROMPT + build_user_message(transcript) + json.dumps(brief_json_schema())
    estimate = estimate_run(model, prompt)
    print(
        f"Estimated input: ~{estimate.input_tokens:,} tokens "
        "(prompt + transcript + schema, at roughly 4 characters per token)."
    )
    if estimate.low_usd is None or estimate.high_usd is None:
        print(f"No list price on file for {model}; see pricing.py.")
        return
    print(
        f"Estimated cost with {model}: {_usd(estimate.low_usd)} to {_usd(estimate.high_usd)} "
        f"per run (assuming {OUTPUT_TOKENS_LOW:,}-{OUTPUT_TOKENS_HIGH:,} output tokens; "
        f"list prices as of {PRICES_AS_OF})."
    )


def _print_result(result: AnalysisResult) -> None:
    print(_parse_summary(result.transcript, result.meta.source))
    print(f"Brief: {result.meta.model}, {result.meta.generated_by}")
    counts = result.verified.counts()
    total = sum(counts.values())
    print(
        f"Quotes checked: {total} | verified {counts['verified']} | "
        f"corrected {counts['corrected']} | unverified {counts['unverified']}"
    )
    for rejected in result.verified.rejected:
        print(f"  rejected: {rejected.section}: {rejected.label}")


def _settings(args: argparse.Namespace) -> VerificationSettings:
    if not 0.5 <= args.fuzzy_threshold <= 1.0:
        raise SystemExit("--fuzzy-threshold must be between 0.5 and 1.0")
    return VerificationSettings(fuzzy_threshold=args.fuzzy_threshold)


# --- Commands ---------------------------------------------------------------------------


def _run_analyze(args: argparse.Namespace) -> int:
    if args.dry_run:
        transcript = load_transcript(args.transcript)
        print(_parse_summary(transcript, args.transcript.name))
        _print_estimate(transcript, resolve_model(args.model))
        return 0
    result = analyze_file(
        args.transcript,
        replay=args.replay,
        record=args.record,
        model=args.model,
        effort=args.effort,
        policy=args.unverified,
        settings=_settings(args),
    )
    _print_result(result)
    for path in write_brief_reports(result, args.out_dir, args.format):
        print(f"Wrote {path}")
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    if args.dry_run:
        for path in (args.previous, args.current):
            transcript = load_transcript(path)
            print(_parse_summary(transcript, path.name))
            _print_estimate(transcript, resolve_model(args.model))
        return 0
    replays = args.replay or (None, None)
    records = args.record or (None, None)
    results = []
    for path, replay, record in zip((args.previous, args.current), replays, records, strict=True):
        result = analyze_file(
            path,
            replay=replay,
            record=record,
            model=args.model,
            effort=args.effort,
            policy=args.unverified,
            settings=_settings(args),
        )
        _print_result(result)
        for written in write_brief_reports(result, args.out_dir, args.format):
            print(f"Wrote {written}")
        results.append(result)
    previous, current = results
    comparison = compare_results(previous, current)
    print(f"Tone: {comparison.tone.description}")
    for change in comparison.guidance:
        print(f"Guidance {change.metric} ({change.period}): {change.change}")
    for path in write_comparison_reports(comparison, previous, current, args.out_dir, args.format):
        print(f"Wrote {path}")
    return 0


def _run_paragraphs(args: argparse.Namespace) -> int:
    transcript = load_transcript(args.transcript)
    print(_parse_summary(transcript, args.transcript.name))
    for p in transcript.paragraphs:
        preview = p.text if len(p.text) <= 90 else p.text[:87] + "..."
        print(f"{p.id}  {p.section:<16} {p.role:<10} {p.speaker or '-':<18} {preview}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {"analyze": _run_analyze, "compare": _run_compare, "paragraphs": _run_paragraphs}
    try:
        return commands[args.command](args)
    except (MissingApiKeyError, LLMError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
