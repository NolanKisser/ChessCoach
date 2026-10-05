"""ChessCoach CLI.

    python -m chesscoach fetch --chesscom <user> --lichess <user> --months 6
    python -m chesscoach analyze --depth 14 --limit 50
    python -m chesscoach report [--json]
    python -m chesscoach coach [--provider anthropic|openai|gemini|ollama|... --model M] [--narrate]
"""
import argparse
import json
import os
import sys
import time

import chess.engine

from . import analyze, coach, fetch, narrate, report, storage
from .config import DATA_DIR, stockfish_path


def cmd_fetch(args) -> None:
    conn = storage.connect()
    if args.chesscom:
        n = storage.upsert_games(conn, fetch.fetch_chesscom(args.chesscom, args.months))
        print(f"Chess.com: {n} new games")
    if args.lichess:
        n = storage.upsert_games(conn, fetch.fetch_lichess(args.lichess, args.months, args.max))
        print(f"Lichess: {n} new games")
    if not (args.chesscom or args.lichess):
        print("Pass --chesscom and/or --lichess usernames.")


def cmd_analyze(args) -> None:
    conn = storage.connect()
    games = storage.games_to_analyze(conn, args.limit)
    backfill = storage.games_missing_mates(conn)
    if not games and not backfill:
        print("Nothing to analyze. Run `fetch` first.")
        return
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path())
    engine.configure({"Threads": args.threads, "Hash": args.hash})
    try:
        if backfill:
            # Games analyzed before mate distances were stored: re-check only the positions
            # whose eval hit the clip, since a forced mate can't hide anywhere else.
            print(f"Finding forced mates in {len(backfill)} previously analyzed games...")
            start = time.perf_counter()
            for i, g in enumerate(backfill, 1):
                positions = storage.clipped_positions(conn, g["id"], analyze.EVAL_CLIP)
                storage.save_mates(conn, g["id"], analyze.find_mates(engine, positions, g["analyzed_depth"]))
                if i % 50 == 0 or i == len(backfill):
                    print(f"  [{i}/{len(backfill)}] ({time.perf_counter() - start:.0f}s)")
        for i, g in enumerate(games, 1):
            start = time.perf_counter()
            moves = analyze.analyze_game(engine, g["pgn"], g["user_color"], args.depth)
            storage.save_analysis(conn, g["id"], args.depth, moves)
            blunders = sum(m["is_user"] and m["classification"] == "blunder" for m in moves)
            print(f"[{i}/{len(games)}] {g['id']}  {len(moves)} plies, {blunders} blunders "
                  f"({time.perf_counter() - start:.1f}s)")
    finally:
        engine.quit()


def cmd_report(args) -> None:
    data = report.build_report(storage.connect())
    print(json.dumps(data, indent=2) if args.json else report.format_report(data))


def cmd_coach(args) -> None:
    data = report.build_report(storage.connect())
    if not data["overview"]["games"]:
        print("No analyzed games yet. Run `fetch` and `analyze` first.")
        return
    if args.narrate and not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit("narrate: Set ELEVENLABS_API_KEY to narrate the plan.")
    try:
        # LLMs emit emoji etc.; Windows pipes/redirects default to cp1252 and would crash.
        sys.stdout.reconfigure(encoding="utf-8")
        provider, model, base_url = coach.resolve(args.provider, args.model, args.base_url)
        print(f"Coaching with {provider} / {model} (may take a minute)...\n", file=sys.stderr)
        plan = []
        for text in coach.stream_coaching(data, provider, model, base_url):
            plan.append(text)
            print(text, end="", flush=True)
        print()
    except coach.CoachError as e:
        raise SystemExit(f"\ncoach: {e}") from None
    if args.narrate:
        print(f"\nNarrating with ElevenLabs to {args.narrate}...", file=sys.stderr)
        try:
            chars = narrate.narrate("".join(plan), args.narrate, args.voice, args.voice_model)
        except narrate.NarrateError as e:
            raise SystemExit(f"narrate: {e}") from None
        print(f"Saved {args.narrate} ({chars} characters narrated).", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="chesscoach")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download games")
    p.add_argument("--chesscom", help="Chess.com username")
    p.add_argument("--lichess", help="Lichess username")
    p.add_argument("--months", type=int, default=3, help="how many recent months (default 3)")
    p.add_argument("--max", type=int, help="max Lichess games")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("analyze", help="run Stockfish over unanalyzed games")
    p.add_argument("--depth", type=int, default=14)
    p.add_argument("--limit", type=int, help="max games this run")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--hash", type=int, default=256, help="engine hash in MB")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="summarize recurring mistakes")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("coach", help="get an LLM-written training plan from the report")
    p.add_argument("--provider", choices=coach.PROVIDERS,
                   help=f"LLM provider (default ${{CHESSCOACH_LLM_PROVIDER}} or {coach.DEFAULT_PROVIDER})")
    p.add_argument("--model", help=f"model name (anthropic default: {coach.DEFAULT_ANTHROPIC_MODEL})")
    p.add_argument("--base-url", help="OpenAI-compatible endpoint, for --provider custom")
    p.add_argument("--narrate", nargs="?", const=str(DATA_DIR / "coach.mp3"), metavar="MP3",
                   help="also read the plan aloud with ElevenLabs into an MP3 "
                        "(default path: data/coach.mp3; needs ELEVENLABS_API_KEY)")
    p.add_argument("--voice", help="ElevenLabs voice ID (default $ELEVENLABS_VOICE_ID or a premade voice)")
    p.add_argument("--voice-model", help=f"ElevenLabs model (default $ELEVENLABS_MODEL or {narrate.DEFAULT_MODEL})")
    p.set_defaults(func=cmd_coach)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
