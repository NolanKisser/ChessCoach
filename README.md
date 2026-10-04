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

## How moves are classified

Evals are converted to win% with Lichess's curve; a move that drops your win chance by
≥5 / ≥10 / ≥15 points is an inaccuracy / mistake / blunder. Phase: endgame when combined
non-pawn material ≤ 26, opening through move 12, middlegame otherwise.

Openings (with ≥ 3 games) are ranked by priority = loss rate + how early and how often your first
mistake lands, so an opening you misplay on move 5 comes before one you misplay on move 14.

## Development

```bash
python -m pytest
```

Tests are pure unit tests — no network or Stockfish required. Issues and pull requests welcome.

## Roadmap

1. ~~Fetch, analyze, pattern report~~
2. LLM coach (Ollama or Claude API) over `report --json` + specific positions
3. Motif tagging: hung pieces, missed forks/mates, back-rank issues
4. Puzzle trainer generated from your own blunders
5. Web UI (FastAPI + board view)

## License

[MIT](LICENSE)
