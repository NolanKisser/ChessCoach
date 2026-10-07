from chesscoach import storage
from chesscoach.report import error_earliness, game_digest, opening_priority, session_numbers


def test_error_earliness_decays_with_move():
    assert error_earliness(None) == 0.0
    assert error_earliness(1) == 1.0
    assert error_earliness(9) == 0.5
    assert error_earliness(5) > error_earliness(14) > error_earliness(30) > 0


def test_early_errors_outrank_late_errors_at_same_score():
    early = opening_priority(50, error_earliness(5))
    late = opening_priority(50, error_earliness(14))
    assert early > late


def test_session_numbers_restart_after_a_break():
    hour = 3600
    times = [0, 600, 1200, 1200 + 2 * hour, None, 1200 + 2 * hour + 900]
    assert session_numbers(times) == [1, 2, 3, 1, None, 2]


def _move(ply, is_user, classification=None, cp_loss=0, clock=None):
    return {"ply": ply, "color": "white" if ply % 2 else "black", "is_user": is_user,
            "san": "e4", "best_san": "e4", "fen_before": "", "eval_before": 0, "eval_after": 0,
            "cp_loss": cp_loss, "win_drop": 0, "classification": classification,
            "phase": "opening", "clock": clock, "mate": None, "mate_pv": None}


def test_game_digest_rows(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    base = {"source": "lichess", "pgn": "", "user_color": "white", "user_rating": 1500,
            "opponent": "x", "opponent_rating": 1600, "time_class": "blitz", "time_control": "180+0",
            "eco": "B20", "opening": "Sicilian Defense"}
    storage.upsert_games(conn, [
        {**base, "id": "lichess:a", "url": "u/a", "result": "loss", "played_at": 1_700_000_000},
        {**base, "id": "lichess:b", "url": "u/b", "result": "win", "played_at": 1_700_000_600},
        {**base, "id": "lichess:c", "url": "u/c", "result": "win", "played_at": 1_700_000_900},
    ])
    storage.save_analysis(conn, "lichess:a", 14, [
        _move(1, 1, cp_loss=10, clock=170), _move(2, 0, "blunder"),
        _move(3, 1, "mistake", cp_loss=150, clock=60), _move(4, 0),
        _move(5, 1, "blunder", cp_loss=400, clock=12)])
    storage.save_analysis(conn, "lichess:b", 14, [_move(1, 1), _move(2, 0)])
    # lichess:c is not analyzed, so it's left out.

    rows = game_digest(conn, limit=5)
    assert [r["url"] for r in rows] == ["u/a", "u/b"]  # oldest first
    a = rows[0]
    assert (a["moves"], a["mistakes"], a["blunders"], a["inaccuracies"]) == (3, 1, 1, 0)
    assert a["acpl"] == round((10 + 150 + 400) / 3, 1)  # user moves only
    assert (a["first_error_move"], a["min_clock"], a["low_clock_blunders"]) == (2, 12, 1)
    assert [r["session_game"] for r in rows] == [1, 2]
    assert rows[1]["first_error_move"] is None and rows[1]["min_clock"] is None
    assert len(a["date"]) == 10 and isinstance(a["hour"], int)
    assert [r["url"] for r in game_digest(conn, limit=1)] == ["u/b"]  # most recent N
