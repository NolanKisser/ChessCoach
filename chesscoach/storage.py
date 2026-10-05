import sqlite3
from pathlib import Path
from typing import Iterable

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    url             TEXT,
    pgn             TEXT NOT NULL,
    user_color      TEXT NOT NULL,
    user_rating     INTEGER,
    opponent        TEXT,
    opponent_rating INTEGER,
    result          TEXT NOT NULL,      -- win / draw / loss, from the user's side
    time_class      TEXT,
    time_control    TEXT,
    eco             TEXT,
    opening         TEXT,
    played_at       INTEGER,            -- unix seconds
    analyzed_depth  INTEGER,            -- NULL until analyzed
    mates_checked   INTEGER             -- 1 once moves.mate is filled in (see analyze.find_mates)
);

CREATE TABLE IF NOT EXISTS moves (
    game_id         TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply             INTEGER NOT NULL,   -- 1 = white's first move
    color           TEXT NOT NULL,
    is_user         INTEGER NOT NULL,
    san             TEXT NOT NULL,
    best_san        TEXT,
    fen_before      TEXT NOT NULL,
    eval_before     INTEGER,            -- centipawns, white POV, clipped
    eval_after      INTEGER,
    cp_loss         INTEGER,            -- mover POV, >= 0
    win_drop        REAL,               -- mover's win% lost, 0-100 scale
    classification  TEXT,               -- inaccuracy / mistake / blunder / NULL
    phase           TEXT,               -- opening / middlegame / endgame
    clock           REAL,               -- seconds left after the move, if known
    mate            INTEGER,            -- forced mate in N from the position before the move,
                                        -- white POV (+N white mates, -N black mates); NULL if none
    mate_pv         TEXT,               -- the mating line, space-separated UCI, when mate is set
    PRIMARY KEY (game_id, ply)
);

CREATE INDEX IF NOT EXISTS idx_moves_user ON moves(is_user, classification);
"""

GAME_COLUMNS = (
    "id", "source", "url", "pgn", "user_color", "user_rating", "opponent",
    "opponent_rating", "result", "time_class", "time_control", "eco",
    "opening", "played_at",
)


# Columns added after the first release: (table, column, type). connect() adds any that an
# older database is missing, so existing data/chesscoach.db files keep working.
ADDED_COLUMNS = (
    ("games", "mates_checked", "INTEGER"),
    ("moves", "mate", "INTEGER"),
    ("moves", "mate_pv", "TEXT"),
)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for table, column, type_ in ADDED_COLUMNS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
    return conn


def upsert_games(conn: sqlite3.Connection, games: Iterable[dict]) -> int:
    """Insert new games; existing ones (and their analysis) are left untouched."""
    sql = (
        f"INSERT OR IGNORE INTO games ({', '.join(GAME_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in GAME_COLUMNS)})"
    )
    before = conn.total_changes
    conn.executemany(sql, ([g[c] for c in GAME_COLUMNS] for g in games))
    conn.commit()
    return conn.total_changes - before


def games_to_analyze(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM games WHERE analyzed_depth IS NULL ORDER BY played_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def save_analysis(conn: sqlite3.Connection, game_id: str, depth: int, moves: list[dict]) -> None:
    conn.execute("DELETE FROM moves WHERE game_id = ?", (game_id,))
    conn.executemany(
        """INSERT INTO moves (game_id, ply, color, is_user, san, best_san, fen_before,
               eval_before, eval_after, cp_loss, win_drop, classification, phase, clock,
               mate, mate_pv)
           VALUES (:game_id, :ply, :color, :is_user, :san, :best_san, :fen_before,
               :eval_before, :eval_after, :cp_loss, :win_drop, :classification, :phase, :clock,
               :mate, :mate_pv)""",
        [{"game_id": game_id, **m} for m in moves],
    )
    conn.execute("UPDATE games SET analyzed_depth = ?, mates_checked = 1 WHERE id = ?",
                 (depth, game_id))
    conn.commit()


def games_missing_mates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Games analyzed before mate distances were stored."""
    return conn.execute("SELECT id, analyzed_depth FROM games "
                        "WHERE analyzed_depth IS NOT NULL AND mates_checked IS NULL").fetchall()


def clipped_positions(conn: sqlite3.Connection, game_id: str, clip: int) -> list[sqlite3.Row]:
    """Positions whose stored eval hit the clip: the only ones that can hide a forced mate."""
    return conn.execute("SELECT ply, fen_before FROM moves WHERE game_id = ? AND ABS(eval_before) = ?",
                        (game_id, clip)).fetchall()


def save_mates(conn: sqlite3.Connection, game_id: str, mates: dict[int, tuple[int, str]]) -> None:
    """mates: ply -> (white-POV mate distance, UCI line). Marks the game as checked."""
    conn.executemany("UPDATE moves SET mate = ?, mate_pv = ? WHERE game_id = ? AND ply = ?",
                     [(m, pv, game_id, ply) for ply, (m, pv) in mates.items()])
    conn.execute("UPDATE games SET mates_checked = 1 WHERE id = ?", (game_id,))
    conn.commit()
