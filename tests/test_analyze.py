import chess

from chesscoach.analyze import classify, game_phase, win_pct
from chesscoach.fetch import _opening_from_eco_url, normalize_chesscom, normalize_lichess


def test_win_pct_is_symmetric_and_centered():
    assert win_pct(0) == 50
    assert round(win_pct(300) + win_pct(-300), 6) == 100
    assert win_pct(1000) > 95


def test_classify_thresholds():
    assert classify(2) is None
    assert classify(5) == "inaccuracy"
    assert classify(12) == "mistake"
    assert classify(40) == "blunder"


def test_game_phase():
    assert game_phase(chess.Board()) == "opening"
    assert game_phase(chess.Board("8/5k2/8/8/8/2R5/5K2/8 w - - 0 40")) == "endgame"
    middlegame = chess.Board()
    middlegame.fullmove_number = 20
    assert game_phase(middlegame) == "middlegame"


def test_opening_from_eco_url():
    url = "https://www.chess.com/openings/Caro-Kann-Defense-Advance-Variation-3...Bf5"
    assert _opening_from_eco_url(url) == "Caro Kann Defense Advance Variation"


def test_normalize_chesscom_user_as_black():
    g = {
        "url": "https://www.chess.com/game/live/123",
        "rules": "chess",
        "time_class": "blitz",
        "time_control": "180",
        "end_time": 1700000000,
        "pgn": '[ECO "B12"]\n[ECOUrl "https://www.chess.com/openings/Caro-Kann-Defense"]\n\n1. e4 c6 *',
        "white": {"username": "Opp", "rating": 1500, "result": "resigned"},
        "black": {"username": "Me", "rating": 1450, "result": "win"},
    }
    game = normalize_chesscom(g, "me")
    assert game["id"] == "chesscom:123"
    assert game["user_color"] == "black"
    assert game["result"] == "win"
    assert game["opening"] == "Caro Kann Defense"
    assert game["eco"] == "B12"


def test_normalize_lichess_draw_and_skips_variants():
    g = {
        "id": "abcd1234", "variant": "standard", "speed": "rapid", "status": "draw",
        "createdAt": 1700000000000, "pgn": "1. e4 e5 *",
        "opening": {"eco": "C20", "name": "King's Pawn Game: Busch-Gass Gambit"},
        "players": {
            "white": {"user": {"name": "Me"}, "rating": 1600},
            "black": {"user": {"name": "Opp"}, "rating": 1650},
        },
    }
    game = normalize_lichess(g, "me")
    assert game["result"] == "draw"
    assert game["opening"] == "King's Pawn Game"
    assert normalize_lichess({**g, "variant": "chess960"}, "me") is None
