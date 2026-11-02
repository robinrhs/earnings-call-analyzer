"""End-to-end runs of the CLI on the fictional examples, using the replay files."""

import json
import subprocess
import sys
from pathlib import Path

from earnings_call_analyzer.cli import main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
Q2, Q3 = EXAMPLES / "fernhill_q2_fy2026.txt", EXAMPLES / "fernhill_q3_fy2026.txt"
R2, R3 = (
    EXAMPLES / "replay" / "fernhill_q2_fy2026.json",
    EXAMPLES / "replay" / "fernhill_q3_fy2026.json",
)


def test_analyze_with_replay_writes_all_formats(tmp_path, capsys):
    code = main(["analyze", str(Q3), "--replay", str(R3), "--out-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Quotes checked: 33 | verified 31 | corrected 1 | unverified 1" in out
    assert "rejected: Headline figures: Net revenue retention (software): 118%" in out
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "fernhill_q3_fy2026.brief.html",
        "fernhill_q3_fy2026.brief.json",
        "fernhill_q3_fy2026.brief.md",
    ]
    data = json.loads((tmp_path / "fernhill_q3_fy2026.brief.json").read_text())
    assert data["policy"] == "drop"
    assert len(data["rejected"]) == 1


def test_flag_policy_keeps_the_fabricated_figure(tmp_path):
    main(
        [
            "analyze",
            str(Q3),
            "--replay",
            str(R3),
            "--unverified",
            "flag",
            "--out-dir",
            str(tmp_path),
            "--format",
            "json",
        ]
    )
    data = json.loads((tmp_path / "fernhill_q3_fy2026.brief.json").read_text())
    statuses = [f["status"] for f in data["headline_figures"]]
    assert statuses.count("unverified") == 1
    assert data["rejected"] == []


def test_compare_with_replay(tmp_path, capsys):
    code = main(
        [
            "compare",
            str(Q2),
            str(Q3),
            "--replay",
            str(R2),
            str(R3),
            "--out-dir",
            str(tmp_path),
            "--format",
            "md",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Tone: 1 notch more positive: balanced -> constructive." in out
    assert "Guidance Free cash flow (FY2026): lowered" in out
    assert (tmp_path / "fernhill_q2_fy2026_vs_fernhill_q3_fy2026.compare.md").exists()


def test_dry_run_prints_estimate_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["analyze", str(Q3), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Estimated input: ~" in out
    assert "Estimated cost with claude-opus-5-5" in out


def test_empty_transcript_is_rejected_before_any_api_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    path = tmp_path / "empty.txt"
    path.write_text("\n\n")
    assert main(["analyze", str(path), "--dry-run"]) == 2
    assert main(["analyze", str(path), "--out-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "found 0 paragraph(s), need at least 3" in err
    assert "Estimated cost" not in err


def test_missing_key_is_a_clean_error(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["analyze", str(Q3), "--out-dir", str(tmp_path)]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_unsupported_file_type_is_a_clean_error(tmp_path, capsys):
    path = tmp_path / "call.docx"
    path.write_text("x")
    assert main(["paragraphs", str(path)]) == 2
    assert "Unsupported transcript format" in capsys.readouterr().err


def test_paragraphs_command(capsys):
    assert main(["paragraphs", str(Q2)]) == 0
    out = capsys.readouterr().out
    assert "P017  qa               analyst    Priya Raman" in out


def test_python_dash_m_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "earnings_call_analyzer", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "analyze" in result.stdout and "compare" in result.stdout
