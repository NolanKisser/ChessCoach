import sqlite3

from chesscoach import storage


def test_connect_adds_columns_missing_from_older_databases(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE games (id TEXT PRIMARY KEY, analyzed_depth INTEGER);
        CREATE TABLE moves (game_id TEXT, ply INTEGER, is_user INTEGER, classification TEXT);
    """)
    old.close()

    conn = storage.connect(path)
    for table, column, _ in storage.ADDED_COLUMNS:
        assert column in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    storage.connect(path)  # idempotent
