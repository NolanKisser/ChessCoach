# ChessCoach

A chess coach for your own games: pulls them from Chess.com and/or Lichess, runs Stockfish over
every move, and finds your *recurring* mistakes — weak openings (prioritized by how early you go
wrong), phases where you bleed centipawns, time-trouble blunders. Everything runs locally; no
accounts, API keys, or logins needed — just your public username.

## Quick start

Requires Python 3.10+ and [Stockfish](https://stockfishchess.org/download/).

```bash
git clone https://github.com/NolanKisser/ChessCoach.git
cd ChessCoach
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Install Stockfish any of these ways (auto-detected in this order):

- set `STOCKFISH_PATH=/path/to/stockfish`
- have `stockfish` on your `PATH` (`brew install stockfish`, `sudo apt install stockfish`)
- unzip a [release](https://github.com/official-stockfish/Stockfish/releases) anywhere under `tools/`

Then point it at your own account(s) — either or both:

```bash
python -m chesscoach fetch --chesscom YOUR_CHESSCOM_NAME --lichess YOUR_LICHESS_NAME --months 6
python -m chesscoach analyze --depth 14 --limit 50   # resumable; rerun to analyze more games
python -m chesscoach report                          # or --json for machine-readable output
python -m chesscoach coach                           # LLM-written training plan (see below)
```

Analysis is the slow part (Stockfish evaluates every position); `--limit` lets you do it
in batches, and re-running `analyze` picks up where it left off.

### Optional settings

| Variable | Purpose |
|---|---|
| `STOCKFISH_PATH` | Engine binary location |
| `CHESSCOACH_DATA` | Data directory (default `data/`) |
| `CHESSCOACH_USER_AGENT` | Chess.com asks API clients to identify themselves; e.g. `ChessCoach/0.1 (you@example.com)` |

Your games and analysis stay on your machine in `data/chesscoach.db` (SQLite, gitignored): a
`games` table and a per-ply `moves` table with evals, best move, centipawn loss, win% drop,
classification, phase, and clock.

## LLM coach

`coach` sends the report to an LLM and streams back a personalized training plan: top
priorities with practice tasks, openings to fix, and your worst positions to review. Bring
your own model. Any of these work:

| Provider | Example | Key |
|---|---|---|
| `anthropic` (default) | `coach` (uses `claude-opus-5-5`) | `ANTHROPIC_API_KEY` |
| `openai` | `coach --provider openai --model <model>` | `OPENAI_API_KEY` |
| `gemini` | `coach --provider gemini --model <model>` | `GEMINI_API_KEY` |
| `openrouter` | `coach --provider openrouter --model <vendor/model>` | `OPENROUTER_API_KEY` |
| `groq`, `mistral`, `deepseek`, `xai` | `coach --provider groq --model <model>` | `GROQ_API_KEY`, ... |
| `ollama` (free, local) | `coach --provider ollama --model <model>` | none: run `ollama pull <model>` first |
| `custom` (any OpenAI-compatible server, e.g. LM Studio, vLLM) | `coach --provider custom --base-url http://localhost:1234/v1 --model <model>` | `CHESSCOACH_LLM_API_KEY` if needed |

Set `CHESSCOACH_LLM_PROVIDER` / `CHESSCOACH_LLM_MODEL` / `CHESSCOACH_LLM_BASE_URL` to avoid
repeating flags. Save a plan with `coach > plan.md`. Only the report summary is sent to
the provider: aggregate stats, opening names, your worst positions, and links to those games.

## How moves are classified

Evals are converted to win% with Lichess's curve; a move that drops your win chance by
≥5 / ≥10 / ≥15 points is an inaccuracy / mistake / blunder. Phase: endgame when combined
non-pawn material ≤ 26, opening through move 12, middlegame otherwise.

Moves are also tagged with tactical motifs, using the engine's best move and the opponent's best
reply: **missed / allowed mate in 1, 2 or 3** (on any move: missing a mate in 2 while still
winning counts) and **back-rank mate**; and, on mistakes and blunders, **hung a piece**,
**allowed / missed a fork**, and **missed winning a piece**.

Openings (with ≥ 3 games) are ranked by priority = loss rate + how early and how often your first
mistake lands, so an opening you misplay on move 5 comes before one you misplay on move 14.

## Development

```bash
python -m pytest
```

Tests are pure unit tests — no network or Stockfish required. Issues and pull requests welcome.

## Roadmap

ChessCoach is currently a command-line tool. Available today: fetch, Stockfish analysis, the
pattern report (with tactical motif tagging), and the LLM coach.

Planned (not built yet):

- Puzzle trainer generated from your own blunders
- Web UI (FastAPI + board view)

## License

[MIT](LICENSE)
