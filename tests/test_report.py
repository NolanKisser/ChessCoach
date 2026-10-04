from chesscoach.report import error_earliness, opening_priority


def test_error_earliness_decays_with_move():
    assert error_earliness(None) == 0.0
    assert error_earliness(1) == 1.0
    assert error_earliness(9) == 0.5
    assert error_earliness(5) > error_earliness(14) > error_earliness(30) > 0


def test_early_errors_outrank_late_errors_at_same_score():
    early = opening_priority(50, error_earliness(5))
    late = opening_priority(50, error_earliness(14))
    assert early > late
