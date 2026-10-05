# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows dev environment; the venv lives in `venv/`.

```bash
venv\Scripts\activate
pip install -r requirements.txt

python -m pytest                                        # all tests
python -m pytest tests/test_analyze.py::test_classify_thresholds   # single test

python -m chesscoach fetch --chesscom <user> --lichess <user> --months 6 [--max N]
python -m chesscoach analyze --depth 14 --limit 50 [--threads 4 --hash 256]
python -m chesscoach report [--json]
python -m chesscoach coach [--provider anthropic|ollama|openai|gemini|... --model M --base-url URL]
```

No linter/formatter is configured. Tests are pure unit tests (no network, no Stockfish) — keep them that way by testing the normalize/classify helpers rather than the fetch/engine paths.

## Environment variables

- `STOCKFISH_PATH` — engine binary. Otherwise resolved from `PATH`, then any `stockfish*.exe`/`stockfish` under `tools/` (`config.stockfish_path`). `tools/stockfish/` currently holds the Stockfish release (gitignored).
- `CHESSCOACH_DATA` — data dir (default `data/`); DB is `chesscoach.db` inside it.
- `CHESSCOACH_USER_AGENT` — Chess.com asks API clients to identify themselves with contact info.

## Architecture

Pipeline of CLI subcommands a CLI subcommand in `chesscoach/__main__.py`, with SQLite (`storage.py`) as the hand-off between stages:

1. **fetch** (`fetch.py`) — Chess.com PubAPI (monthly archives) and Lichess (NDJSON stream with `pgnInJson`, clocks, openings). Each source has a `normalize_*` function that maps to a common game dict whose keys are exactly `storage.GAME_COLUMNS`. Game IDs are prefixed `chesscom:` / `lichess:`. `result` is always from the *user's* perspective (win/draw/loss). Requests are serial with 429 backoff (Lichess wants a 60s wait). `upsert_games` uses `INSERT OR IGNORE`, so re-fetching never clobbers existing games or their analysis.
2. **analyze** (`analyze.py`) — resumable: only games with `analyzed_depth IS NULL` are processed, and `save_analysis` replaces that game's `moves` rows and sets `analyzed_depth`. Per ply it stores white-POV evals (clipped to ±1000cp; mate = ±100000 before clipping), plus any forced mate as `mate` (white-POV moves to mate) and `mate_pv` (UCI line, cut at the mate), converts to the mover's POV, then computes `cp_loss` and `win_drop` via Lichess's win% curve. If the played move equals the engine's best move, loss is forced to 0 (avoids depth-noise false positives). Classification thresholds on win% drop: 5/10/15 → inaccuracy/mistake/blunder. Phase: endgame if combined non-pawn material ≤ 26, else opening through fullmove 12, else middlegame.
3. **report** (`report.py`) — SQL aggregations over `is_user = 1` moves: overview, per-phase, per-opening (grouped by opening name + color, min 3 games, ranked by `priority` = loss rate + how early/often the first mistake-or-blunder lands, via an exponential decay with an 8-move half-life), time trouble (clock < 30s), tactical motifs, worst blunders. `build_report` returns plain JSON-serializable dicts deliberately — `report --json` is the intended input for the planned LLM coaching layer; `format_report` is just the CLI rendering.
   Motifs (`motifs.py`) are computed at report time, not stored: for each user move, `tag_error` inspects the engine's best move (row p) and the opponent's best reply (row p+1's `best_san`) with python-chess. The engine having chosen the move is what validates the tactic; the helpers only classify its shape. Mate motifs (missed/allowed mate in 1-3, back-rank) use the stored `moves.mate` distances and apply to every move; hung piece / missed capture / forks only to mistakes and blunders (on good moves they'd be sacrifices).
4. **coach** (`coach.py`) — sends `build_report` output (top openings only, compact JSON) to an LLM and streams a Markdown training plan. Three adapters: Anthropic SDK (default `claude-opus-5-5`, with server-side refusal fallbacks), Ollama's native `/api/chat` (its OpenAI-compatible endpoint ignores `num_ctx`, and the 4096-token default is too small for prompt + reasoning), and the OpenAI SDK for every other OpenAI-compatible provider (`OPENAI_COMPATIBLE` maps name → base URL + key env var). SDKs are imported lazily. Provider/model/base URL resolve from flags → `CHESSCOACH_LLM_*` env → defaults in `coach.resolve`.

Schema lives as a `CREATE TABLE IF NOT EXISTS` script in `storage.SCHEMA`, applied on every `connect()`; new columns go in both the `CREATE TABLE` and `storage.ADDED_COLUMNS`, which `connect()` uses to `ALTER TABLE` older databases; other schema changes need manual handling of existing `data/chesscoach.db` files. `analyze` backfills `mate` for games analyzed before it existed (`games.mates_checked IS NULL`) by re-evaluating only positions whose eval hit the ±1000 clip, since a mate can't hide anywhere else.

## Roadmap (from README)

Next milestones: puzzle trainer from the user's own blunders; FastAPI web UI with board view.
