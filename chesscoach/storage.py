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
    analyzed_depth  INTEGER             -- NULL until analyzed
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
    PRIMARY KEY (game_id, ply)
);

CREATE INDEX IF NOT EXISTS idx_moves_user ON moves(is_user, classification);
"""

GAME_COLUMNS = (
    "id", "source", "url", "pgn", "user_color", "user_rating", "opponent",
    "opponent_rating", "result", "time_class", "time_control", "eco",
    "opening", "played_at",
)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
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
               eval_before, eval_after, cp_loss, win_drop, classification, phase, clock)
           VALUES (:game_id, :ply, :color, :is_user, :san, :best_san, :fen_before,
               :eval_before, :eval_after, :cp_loss, :win_drop, :classification, :phase, :clock)""",
        [{"game_id": game_id, **m} for m in moves],
    )
    conn.execute("UPDATE games SET analyzed_depth = ? WHERE id = ?", (depth, game_id))
    conn.commit()
