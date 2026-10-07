import pytest

from chesscoach import narrate
from chesscoach.narrate import NarrateError, chunk, speakable


@pytest.mark.parametrize("san, spoken", [
    ("Nxe5", "knight takes e5"),
    ("exd5", "e takes d5"),
    ("Qh7#", "queen h7 mate"),
    ("Rae1+", "rook a e1 check"),
    ("e8=Q", "e8 promotes to queen"),
    ("O-O-O", "castles queenside"),
    ("O-O", "castles"),
    ("e4", "e4"),  # bare square: left alone
])
def test_speakable_spells_out_moves(san, spoken):
    assert speakable(f"On move 26 you played {san}.") == f"On move 26 you played {spoken}."


def test_speakable_strips_markdown_links_and_fens():
    md = ("## 1. **Big picture**\n\n"
          "- You blundered with *Bxf7* ([game](https://lichess.org/abc123)) in "
          "`r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3`.\n"
          "- See https://www.chess.com/game/live/1 for more. Watch missed_mate_in_2.")
    assert speakable(md) == ("Big picture\n\n"
                             "You blundered with bishop takes f7 (game) in.\n"
                             "See for more. Watch missed mate in 2.")


def test_speakable_black_move_numbers():
    assert speakable("Avoid 12...Qxb2.") == "Avoid 12, black plays queen takes b2."


def test_chunk_packs_paragraphs_under_limit():
    text = "\n\n".join(["a" * 40, "b" * 40, "c" * 40])
    assert chunk(text, limit=90) == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]


def test_chunk_splits_long_paragraph_by_sentence_then_space():
    para = "First sentence here. " + "word " * 30
    pieces = chunk(para.strip(), limit=50)
    assert pieces[0].startswith("First sentence here.")
    assert all(len(p) <= 50 for p in pieces)
    assert " ".join(pieces).split() == para.split()


def test_resolve_prefers_args_then_env(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.setenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
    assert narrate.resolve(None, None) == (narrate.DEFAULT_VOICE_ID, "eleven_flash_v2_5")
    assert narrate.resolve("v", "m") == ("v", "m")


def test_narrate_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(NarrateError, match="ELEVENLABS_API_KEY"):
        narrate.narrate("Hello.", str(tmp_path / "out.mp3"))


class FakeResponse:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code, self.content, self._payload = status, content, payload
        self.ok = status < 400
        self.text = str(payload)

    def json(self):
        return self._payload


def test_narrate_concatenates_chunks(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(narrate, "MAX_CHUNK_CHARS", 20)
    calls = []

    def fake_post(url, params, headers, json, timeout):
        calls.append(json)
        return FakeResponse(content=f"<{len(calls)}>".encode())

    monkeypatch.setattr(narrate.requests, "post", fake_post)
    out = tmp_path / "out.mp3"
    narrate.narrate("First paragraph.\n\nSecond paragraph.", str(out), voice="v", model="m")
    assert out.read_bytes() == b"<1><2>"
    assert [c["text"] for c in calls] == ["First paragraph.", "Second paragraph."]
    assert calls[0]["next_text"] == "Second paragraph." and "previous_text" not in calls[0]
    assert calls[1]["previous_text"] == "First paragraph." and calls[1]["model_id"] == "m"


def test_narrate_surfaces_api_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(narrate.requests, "post", lambda *a, **kw: FakeResponse(
        429, payload={"detail": {"status": "quota_exceeded", "message": "Quota exceeded."}}))
    out = tmp_path / "out.mp3"
    with pytest.raises(NarrateError, match="429: Quota exceeded."):
        narrate.narrate("Hello.", str(out))
    assert not out.exists()
