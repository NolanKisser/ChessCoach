"""Aggregate analyzed moves into a structured picture of the user's recurring mistakes.

build_report() returns plain dicts so the same data can feed the CLI now and the
LLM coaching layer later.
"""
import sqlite3
import statistics

from . import motifs

TIME_TROUBLE_SECONDS = 30
MOTIF_LABELS = {
    "hung_piece": "Hung a piece",
    "allowed_fork": "Allowed a fork",
    "missed_capture": "Missed winning a piece",
    "missed_fork": "Missed a fork",
    "back_rank": "Allowed back-rank mate",
}


def motif_label(tag: str) -> str:
    for prefix, label in (("missed_mate_in_", "Missed mate in "), ("allowed_mate_in_", "Allowed mate in ")):
        if tag.startswith(prefix):
            return label + tag[len(prefix):]
    return MOTIF_LABELS.get(tag, tag)
MIN_OPENING_GAMES = 3
# A first big error this many moves later counts half as much toward an opening's priority.
EARLY_ERROR_HALF_LIFE = 8


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


def error_earliness(first_error_move: int | None) -> float:
    """1.0 for an error on move 1, decaying toward 0 the later it comes; 0 if no error."""
    if not first_error_move:
        return 0.0
    return 0.5 ** ((first_error_move - 1) / EARLY_ERROR_HALF_LIFE)


def opening_priority(score_pct: float, early_error_score: float) -> float:
    """Higher = study this opening first. Equal weight on losing and on erring early."""
    return round((100 - score_pct) / 100 + early_error_score, 3)


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
        score_pct = round(100 * score / len(gs), 1)
        first_errors = [(g["first_error_ply"] + 1) // 2 if g["first_error_ply"] else None for g in gs]
        found = [m for m in first_errors if m]
        # Averaged over all games, so error-free games pull it down: rewards early AND frequent errors.
        early_error_score = round(statistics.mean(error_earliness(m) for m in first_errors), 3)
        out.append({
            "opening": opening, "color": color, "games": len(gs),
            "score_pct": score_pct,
            "acpl": round(statistics.mean(g["acpl"] for g in gs), 1),
            "median_first_error_move": statistics.median(found) if found else None,
            "early_error_score": early_error_score,
            "priority": opening_priority(score_pct, early_error_score),
        })
    return sorted(out, key=lambda r: (-r["priority"], -r["acpl"]))


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


def by_motif(conn: sqlite3.Connection) -> list[dict]:
    """Tactical motifs behind the user's mistakes and blunders (plus missed/allowed short mates
    on any move), most frequent first."""
    rows = conn.execute("""
        SELECT m.ply, m.san, m.best_san, m.fen_before, m.win_drop, m.classification, m.mate,
               n.best_san AS reply_san, n.mate AS reply_mate, n.mate_pv AS reply_mate_pv,
               g.url, g.user_color
        FROM moves m
        JOIN games g ON g.id = m.game_id
        LEFT JOIN moves n ON n.game_id = m.game_id AND n.ply = m.ply + 1
        WHERE m.is_user = 1 AND (m.classification IN ('mistake', 'blunder')
              OR m.mate IS NOT NULL OR n.mate IS NOT NULL
              OR m.best_san LIKE '%#' OR n.best_san LIKE '%#')
    """).fetchall()
    games = conn.execute("SELECT COUNT(*) FROM games WHERE analyzed_depth IS NOT NULL").fetchone()[0]

    stats: dict[str, dict] = {}
    for r in rows:
        sign = 1 if r["user_color"] == "white" else -1
        tags = motifs.tag_error(
            r["fen_before"], r["san"], r["best_san"], r["reply_san"],
            mate_before=r["mate"] * sign if r["mate"] is not None else None,
            mate_after=r["reply_mate"] * sign if r["reply_mate"] is not None else None,
            reply_mate_pv=r["reply_mate_pv"],
            is_error=r["classification"] in ("mistake", "blunder"),
        )
        for tag in tags:
            s = stats.setdefault(tag, {"count": 0, "drop": 0.0, "worst": r})
            s["count"] += 1
            s["drop"] += r["win_drop"]
            if r["win_drop"] > s["worst"]["win_drop"]:
                s["worst"] = r

    out = []
    for tag, s in stats.items():
        w = s["worst"]
        out.append({
            "motif": tag, "count": s["count"],
            "per_100_games": _rate(s["count"], games),
            "avg_win_drop": round(s["drop"] / s["count"], 1),
            "worst_example": {
                "move_number": (w["ply"] + 1) // 2, "user_color": w["user_color"], "san": w["san"],
                "best_san": w["best_san"], "opponent_best_reply": w["reply_san"],
                "win_drop": w["win_drop"], "fen_before": w["fen_before"], "url": w["url"],
            },
        })
    return sorted(out, key=lambda m: -m["count"])


def build_report(conn: sqlite3.Connection) -> dict:
    return {
        "overview": overview(conn),
        "phases": by_phase(conn),
        "openings": by_opening(conn),
        "time_trouble": time_trouble(conn),
        "motifs": by_motif(conn),
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

    lines += ["", f"Openings (>= {MIN_OPENING_GAMES} games, highest priority first):"]
    for op in report["openings"][:10]:
        first = op["median_first_error_move"]
        lines.append(f"  prio {op['priority']:.2f}  {op['score_pct']:>5}%  {op['games']:>3} games  ACPL {op['acpl']:>6}  "
                     f"{op['color']:<5}  {op['opening']}"
                     + (f"  (first big error ~move {first})" if first else ""))
    if not report["openings"]:
        lines.append("  (not enough games per opening yet)")

    lines += ["", "Tactical motifs (mates: any move; others: mistakes/blunders only):"]
    for m in report["motifs"]:
        lines.append(f"  {motif_label(m['motif']):<26} {m['count']:>4}  "
                     f"({m['per_100_games']} per 100 games)")
    if not report["motifs"]:
        lines.append("  (none found)")

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
