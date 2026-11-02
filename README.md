# earnings-call-analyzer

**Status: work in progress.** `main` currently holds only the project skeleton. The analyzer is implemented on feature branches and is merged into `main` through pull requests, one functional step at a time. This README is replaced by the full documentation (method, formulas, example output, cost estimate) when the command-line tool and the examples land.

## Goal

Turn an earnings call transcript into a structured analyst brief with Claude, where every claim carries a verbatim quote that is checked automatically against the transcript. It is meant as a research aid with an audit trail. It is not investment advice and does not produce recommendations.

## What is on `main` today

- A uv project with a `src/` layout (`src/earnings_call_analyzer`), Python 3.11 or newer
- MIT license, `.gitignore` and an `.env.example` with an empty `ANTHROPIC_API_KEY`
- A GitHub Actions workflow that runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest` on Python 3.11 and 3.12
- One smoke test that imports the package and checks its version against the installed metadata

There is no working analyzer on `main` yet. The `earnings-call-analyzer` console script declared in `pyproject.toml` points to a CLI module that arrives with a later pull request, so it fails until then.

## Coming next, in integration order

1. Transcript parser: `.txt` and `.pdf` transcripts split into numbered paragraphs (`P001`, ...) tagged with speaker, role and section
2. Brief schema with mandatory evidence per item, quote verification against the transcript, and the Claude extraction with structured outputs and record/replay
3. Quarter-over-quarter comparison of two verified briefs, and Markdown and HTML reports with verification badges
4. The `analyze`, `compare` and `paragraphs` commands, two fictional example transcripts with their outputs, and the full README
5. Follow-up fixes: stricter quote verification and QoQ rules; unit-aware number checks and sign wording; clean errors for broken replay files, fallback cost accounting and an honest report banner

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

## License

MIT, see [LICENSE](LICENSE).
