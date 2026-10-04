# ChessCoach

A personal chess coach: pulls your games from Chess.com and Lichess, runs Stockfish over every
move, and finds your *recurring* mistakes — weak openings, phases where you bleed centipawns,
time-trouble blunders. An LLM coaching layer (milestone 2) will explain the patterns in plain language.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Stockfish: download from https://github.com/official-stockfish/Stockfish/releases and unzip
under `tools/` (auto-detected), or set `STOCKFISH_PATH`.

Chess.com asks API clients to identify themselves — set e.g.
`CHESSCOACH_USER_AGENT="ChessCoach/0.1 (you@example.com)"`.

## Usage

```bash
python -m chesscoach fetch --chesscom <user> --lichess <user> --months 6
python -m chesscoach analyze --depth 14 --limit 50   # resumable; only unanalyzed games
python -m chesscoach report                          # or --json for the LLM layer
```

Data lives in `data/chesscoach.db` (SQLite): a `games` table and a per-ply `moves` table with
evals, best move, centipawn loss, win% drop, classification, phase, and clock.

## How moves are classified

Evals are converted to win% with Lichess's curve; a move that drops your win chance by
≥5 / ≥10 / ≥15 points is an inaccuracy / mistake / blunder. Phase: endgame when combined
non-pawn material ≤ 26, opening through move 12, middlegame otherwise.

## Roadmap

1. ~~Fetch, analyze, pattern report~~
2. LLM coach (Ollama or Claude API) over `report --json` + specific positions
3. Motif tagging: hung pieces, missed forks/mates, back-rank issues
4. Puzzle trainer generated from your own blunders
5. Web UI (FastAPI + board view)
