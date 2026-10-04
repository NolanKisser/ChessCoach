"""ChessCoach CLI.

    python -m chesscoach fetch --chesscom <user> --lichess <user> --months 6
    python -m chesscoach analyze --depth 14 --limit 50
    python -m chesscoach report [--json]
"""
import argparse
import json
import time

import chess.engine

from . import analyze, fetch, report, storage
from .config import stockfish_path


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
    if not games:
        print("Nothing to analyze. Run `fetch` first.")
        return
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path())
    engine.configure({"Threads": args.threads, "Hash": args.hash})
    try:
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
