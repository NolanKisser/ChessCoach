import pytest

from chesscoach.motifs import tag_error


@pytest.mark.parametrize("fen, san, best, reply, expected", [
    # Missed Ra8# on a boxed-in king.
    ("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "h3", "Ra8#", None, {"missed_mate_in_1"}),
    # White ignores the back rank; black mates with Ra1#.
    ("r5k1/5ppp/8/8/8/8/1P3PPP/6K1 w - - 0 1", "b3", "Kf1", "Ra1#", {"allowed_mate_in_1", "back_rank"}),
    # Bishop walks into a pawn.
    ("4k3/8/5p2/8/8/8/8/2B1K3 w - - 0 1", "Bg5", "Kd2", "fxg5", {"hung_piece"}),
    # Undefended queen left en prise.
    ("4k3/8/8/3q4/8/8/8/3QK3 w - - 0 1", "Kf2", "Qxd5", None, {"missed_capture"}),
    # Knight fork of king and rook allowed...
    ("4k3/8/8/8/1n6/8/7P/R3K3 w - - 0 1", "h3", "Kd2", "Nc2+", {"allowed_fork"}),
    # ...and missed.
    ("4k3/8/8/8/1n6/8/7P/R3K3 b - - 0 1", "Kd7", "Nc2+", None, {"missed_fork"}),
])
def test_tag_error(fen, san, best, reply, expected):
    assert set(tag_error(fen, san, best, reply)) == expected


def test_equal_trade_is_not_a_hung_piece():
    # Bxc6 bxc6: bishop for knight.
    fen = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 2 3"
    assert "hung_piece" not in tag_error(fen, "Bxc6", "O-O", "dxc6")


def test_bad_trade_is_a_hung_piece():
    # Queen grabs a defended pawn and is taken.
    fen = "4k3/8/2p5/3p4/8/8/8/3QK3 w - - 0 1"
    assert "hung_piece" in tag_error(fen, "Qxd5", "Kd2", "cxd5")


def test_quiet_move_has_no_tags():
    assert tag_error("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "Kd2", "Ke2", "Kd8") == []


def test_mate_on_back_rank_without_own_pieces_boxing_king_is_not_back_rank():
    # Qd8# against an uncastled king with e7 open: mate, but not the back-rank pattern.
    fen = "rn2k2r/1pp2ppp/p3p1q1/2b5/5N2/2P2RB1/PP1Q2PP/2KR4 b kq - 2 16"
    assert tag_error(fen, "Qh6", "Qg5", "Qd8#") == ["allowed_mate_in_1"]


BACK_RANK_2 = "3qr1k1/p4ppp/8/8/8/8/4RPPP/4R1K1 b - - 0 1"  # Rxe8+ Qxe8 Rxe8# is coming


def test_allowed_back_rank_mate_in_2_from_engine_line():
    tags = tag_error(BACK_RANK_2, "a6", "h6", "Rxe8+", mate_before=None, mate_after=-2,
                     reply_mate_pv="e2e8 d8e8 e1e8", is_error=False)
    assert tags == ["allowed_mate_in_2", "back_rank"]


@pytest.mark.parametrize("mate_before, mate_after, expected", [
    (2, None, ["missed_mate_in_2"]),   # had mate in 2, it's gone
    (3, -5, ["missed_mate_in_3"]),     # and now getting mated (but not within 3)
    (2, 3, []),                        # still mating, just slower
    (4, None, []),                     # beyond MAX_MATE
])
def test_missed_mate_lengths(mate_before, mate_after, expected):
    fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    assert tag_error(fen, "Kd2", "Ke2", "Kd8", mate_before, mate_after, is_error=False) == expected


def test_missed_mate_needs_a_reply_beyond_mate_in_1():
    # Game ended right after the move (resign/timeout): can't tell if the mate was given up.
    assert tag_error("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "Kd2", "Ke2", None, 2, None) == []


def test_allowed_mate_when_already_being_mated_is_not_tagged():
    assert tag_error(BACK_RANK_2, "a6", "h6", "Rxe8+", mate_before=-3, mate_after=-2,
                     reply_mate_pv="e2e8 d8e8 e1e8", is_error=False) == []
