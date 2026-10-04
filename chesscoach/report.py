"""Aggregate analyzed moves into a structured picture of the user's recurring mistakes.

build_report() returns plain dicts so the same data can feed the CLI now and the
LLM coaching layer later.
"""
import sqlite3
import statistics

TIME_TROUBLE_SECONDS = 30
MIN_OPENING_GAMES = 3


def _rate(count: int, total: int, per: int = 100) -> float:
    return round(per * count / total, 2) if total else 0.0


def overview(conn: sqlite3.Connection) -> dict:
    row = conn.execute("""
        SELECT COUNT(*) AS games,
               SUM(result = 'win') AS wins, SUM(result = 'draw') AS draws,
               SUM(result = 'loss') AS losses
        FROM games WHERE analyzed_depth IS NOT NULL
    """).fetchone()
    moves = conn.execute("""
        SELECT COUNT(*) AS n, AVG(cp_loss) AS acpl,
               SUM(classification = 'blunder') AS blunders,
               SUM(classification = 'mistake') AS mistakes,
               SUM(classification = 'inaccuracy') AS inaccuracies
        FROM moves WHERE is_user = 1
    """).fetchone()
    games = row["games"] or 0
    return {
        "games": games,
        "wins": row["wins"] or 0, "draws": row["draws"] or 0, "losses": row["losses"] or 0,
        "acpl": round(moves["acpl"] or 0, 1),
        "blunders_per_game": round((moves["blunders"] or 0) / games, 2) if games else 0,
        "mistakes_per_game": round((moves["mistakes"] or 0) / games, 2) if games else 0,
        "inaccuracies_per_game": round((moves["inaccuracies"] or 0) / games, 2) if games else 0,
    }


def by_phase(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT phase, COUNT(*) AS moves, AVG(cp_loss) AS acpl,
               SUM(classification = 'blunder') AS blunders,
               SUM(classification = 'mistake') AS mistakes
        FROM moves WHERE is_user = 1 GROUP BY phase
    """).fetchall()
    order = {"opening": 0, "middlegame": 1, "endgame": 2}
    return sorted(
        ({
            "phase": r["phase"], "moves": r["moves"], "acpl": round(r["acpl"] or 0, 1),
            "blunders_per_100": _rate(r["blunders"], r["moves"]),
            "mistakes_per_100": _rate(r["mistakes"], r["moves"]),
        } for r in rows),
        key=lambda r: order.get(r["phase"], 9),
    )


def by_opening(conn: sqlite3.Connection, min_games: int = MIN_OPENING_GAMES) -> list[dict]:
    games = conn.execute("""
        SELECT g.id, g.opening, g.user_color, g.result,
               AVG(m.cp_loss) AS acpl,
               MIN(CASE WHEN m.classification IN ('mistake', 'blunder') THEN m.ply END) AS first_error_ply
        FROM games g JOIN moves m ON m.game_id = g.id AND m.is_user = 1
        WHERE g.opening IS NOT NULL
        GROUP BY g.id
    """).fetchall()

    groups: dict[tuple, list] = {}
    for g in games:
        groups.setdefault((g["opening"], g["user_color"]), []).append(g)

    out = []
    for (opening, color), gs in groups.items():
        if len(gs) < min_games:
            continue
        score = sum(1 if g["result"] == "win" else 0.5 if g["result"] == "draw" else 0 for g in gs)
        first_errors = [(g["first_error_ply"] + 1) // 2 for g in gs if g["first_error_ply"]]
        out.append({
            "opening": opening, "color": color, "games": len(gs),
            "score_pct": round(100 * score / len(gs), 1),
            "acpl": round(statistics.mean(g["acpl"] for g in gs), 1),
            "median_first_error_move": statistics.median(first_errors) if first_errors else None,
        })
    return sorted(out, key=lambda r: (r["score_pct"], -r["acpl"]))


def time_trouble(conn: sqlite3.Connection, seconds: int = TIME_TROUBLE_SECONDS) -> dict | None:
    rows = conn.execute("""
        SELECT clock < ? AS low, COUNT(*) AS moves,
               SUM(classification = 'blunder') AS blunders
        FROM moves WHERE is_user = 1 AND clock IS NOT NULL GROUP BY low
    """, (seconds,)).fetchall()
    if not rows:
        return None
    stats = {bool(r["low"]): r for r in rows}
    low, normal = stats.get(True), stats.get(False)
    return {
        "threshold_seconds": seconds,
        "low_clock_moves": low["moves"] if low else 0,
        "low_clock_blunders_per_100": _rate(low["blunders"], low["moves"]) if low else 0.0,
        "normal_blunders_per_100": _rate(normal["blunders"], normal["moves"]) if normal else 0.0,
    }


def worst_moments(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute("""
        SELECT m.ply, m.san, m.best_san, m.fen_before, m.win_drop, m.phase,
               m.eval_before, m.eval_after, g.url, g.opening, g.user_color
        FROM moves m JOIN games g ON g.id = m.game_id
        WHERE m.is_user = 1 AND m.classification = 'blunder'
        ORDER BY m.win_drop DESC LIMIT ?
    """, (limit,)).fetchall()
    return [{**dict(r), "move_number": (r["ply"] + 1) // 2} for r in rows]


def build_report(conn: sqlite3.Connection) -> dict:
    return {
        "overview": overview(conn),
        "phases": by_phase(conn),
        "openings": by_opening(conn),
        "time_trouble": time_trouble(conn),
        "worst_moments": worst_moments(conn),
    }


def format_report(report: dict) -> str:
    o = report["overview"]
    lines = [
        f"Games analyzed: {o['games']}  (W {o['wins']} / D {o['draws']} / L {o['losses']})",
        f"Avg centipawn loss: {o['acpl']}   per game: {o['blunders_per_game']} blunders, "
        f"{o['mistakes_per_game']} mistakes, {o['inaccuracies_per_game']} inaccuracies",
        "",
        "By phase:",
    ]
    for p in report["phases"]:
        lines.append(f"  {p['phase']:<11} ACPL {p['acpl']:>6}   blunders/100 moves {p['blunders_per_100']:>5}"
                     f"   mistakes/100 {p['mistakes_per_100']:>5}")

    lines += ["", f"Openings (>= {MIN_OPENING_GAMES} games, weakest first):"]
    for op in report["openings"][:10]:
        first = op["median_first_error_move"]
        lines.append(f"  {op['score_pct']:>5}%  {op['games']:>3} games  ACPL {op['acpl']:>6}  "
                     f"{op['color']:<5}  {op['opening']}"
                     + (f"  (first big error ~move {first})" if first else ""))
    if not report["openings"]:
        lines.append("  (not enough games per opening yet)")

    tt = report["time_trouble"]
    if tt:
        lines += ["", f"Time trouble (< {tt['threshold_seconds']}s on clock):",
                  f"  blunders/100 moves: {tt['low_clock_blunders_per_100']} with low clock vs "
                  f"{tt['normal_blunders_per_100']} otherwise ({tt['low_clock_moves']} low-clock moves)"]

    lines += ["", "Biggest blunders:"]
    for m in report["worst_moments"]:
        dots = "." if m["user_color"] == "white" else "..."
        lines.append(f"  {m['move_number']}{dots}{m['san']} (best {m['best_san']})  "
                     f"-{m['win_drop']:.0f}% win chance  [{m['phase']}]  {m['url']}")
    return "\n".join(lines)
