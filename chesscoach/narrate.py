"""Optional voice narration of the coaching plan via ElevenLabs text-to-speech.

The plan is Markdown written to be read, so `speakable` first turns it into something a voice
can say: formatting, links and FENs are dropped and SAN moves are spelled out ("Nxe5" ->
"knight takes e5"). The text is then sent to ElevenLabs in paragraph-aligned chunks (each
request has a per-model character limit) and the returned MP3 pieces are concatenated, which
MP3 frames tolerate.
"""
import os
import re

import requests

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George", a premade voice available on every account
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
# Under every current model's per-request limit; the plan itself is usually one or two chunks.
MAX_CHUNK_CHARS = 2500

PIECES = {"K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight"}
SAN_RE = re.compile(
    r"\b(?P<piece>[KQRBN])?(?P<from>[a-h]?[1-8]?)(?P<x>x)?(?P<to>[a-h][1-8])"
    r"(?:=(?P<promo>[QRBN]))?(?P<check>[+#])?(?![\w])"
)
FEN_RE = re.compile(r"\b(?:[pnbrqkPNBRQK1-8]{1,8}/){7}[pnbrqkPNBRQK1-8]{1,8}(?: [wb] \S+ \S+ \d+ \d+)?")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL_RE = re.compile(r"<?https?://[^\s<>()]*[^\s<>().,;:!?]>?")


class NarrateError(Exception):
    """A user-facing problem (missing key, quota, unreachable API)."""


def _say_move(m: re.Match) -> str:
    piece, frm, to = m["piece"], m["from"], m["to"]
    # A bare square ("e4", "the d5 square") with nothing move-like about it stays as is.
    if not (piece or m["x"] or m["promo"] or m["check"]):
        return m[0]
    words = [PIECES[piece]] if piece else ([frm] if frm else [])
    if piece and frm:
        words.append(frm)
    if m["x"]:
        words.append("takes")
    words.append(to)
    if m["promo"]:
        words.append(f"promotes to {PIECES[m['promo']]}")
    if m["check"]:
        words.append("mate" if m["check"] == "#" else "check")
    return " ".join(words)


def speakable(markdown: str) -> str:
    """Turn the coach's Markdown into plain text that reads well aloud."""
    text = LINK_RE.sub(r"\1", markdown)
    text = URL_RE.sub("", text)
    text = FEN_RE.sub("", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", text, flags=re.M)       # headings
    text = re.sub(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]+", "", text, flags=re.M)  # list markers
    text = re.sub(r"^[ \t]*\|.*\|[ \t]*$", "", text, flags=re.M)          # table rows
    text = re.sub(r"^[ \t]*(?:-{3,}|\*{3,})[ \t]*$", "", text, flags=re.M)  # rules
    text = re.sub(r"(?<=[A-Za-z])_(?=\w)", " ", text)            # missed_mate_in_2
    text = re.sub(r"(\*\*|__|\*|_)(\S(?:.*?\S)?)\1", r"\2", text)   # bold / italics
    text = re.sub(r"\bO-O-O\b", "castles queenside", text)
    text = re.sub(r"\bO-O\b", "castles", text)
    text = re.sub(r"\b(\d+)\.\.\.\s*", r"\1, black plays ", text)
    text = SAN_RE.sub(_say_move, text)
    text = re.sub(r"\(\s*\)", "", text)       # parentheses emptied by dropped links/FENs
    text = re.sub(r"[ \t]+([,.;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split on paragraph (then sentence) boundaries into pieces of at most `limit` chars."""
    pieces = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        parts = [para] if len(para) <= limit else re.split(r"(?<=[.!?])\s+", para)
        for part in parts:
            while len(part) > limit:  # a single overlong sentence: hard split at a space
                cut = part.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                pieces.append(part[:cut])
                part = part[cut:].lstrip()
            pieces.append(part)
    # Re-pack small pieces so we make as few requests as possible.
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 2 + len(piece) <= limit:
            chunks[-1] += "\n\n" + piece
        else:
            chunks.append(piece)
    return chunks


def resolve(voice: str | None, model: str | None) -> tuple[str, str]:
    voice = voice or os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID
    model = model or os.environ.get("ELEVENLABS_MODEL") or DEFAULT_MODEL
    return voice, model


def narrate(markdown: str, path: str, voice: str | None = None, model: str | None = None) -> int:
    """Synthesize the plan to an MP3 at `path`; returns the number of characters sent."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise NarrateError("Set ELEVENLABS_API_KEY to narrate the plan.")
    voice, model = resolve(voice, model)
    chunks = chunk(speakable(markdown), MAX_CHUNK_CHARS)
    if not chunks:
        raise NarrateError("Nothing to narrate.")

    audio = bytearray()
    for i, text in enumerate(chunks):
        body = {"text": text, "model_id": model}
        # Neighboring text keeps intonation continuous across chunk boundaries.
        if i:
            body["previous_text"] = chunks[i - 1][-500:]
        if i + 1 < len(chunks):
            body["next_text"] = chunks[i + 1][:500]
        try:
            resp = requests.post(
                API_URL.format(voice_id=voice), params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": api_key, "Accept": "audio/mpeg"}, json=body, timeout=(10, 300),
            )
        except requests.RequestException:
            raise NarrateError("Could not reach the ElevenLabs API. Check your connection.") from None
        if resp.status_code == 401:
            raise NarrateError("ElevenLabs rejected the API key in ELEVENLABS_API_KEY "
                               "(or it lacks text-to-speech permission).")
        if resp.status_code == 404:
            raise NarrateError(f"ElevenLabs has no voice {voice!r}.")
        if not resp.ok:
            raise NarrateError(f"ElevenLabs error {resp.status_code}: {_error_detail(resp)}")
        audio += resp.content

    with open(path, "wb") as f:
        f.write(audio)
    return sum(map(len, chunks))


def _error_detail(resp: requests.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return resp.text[:200]
    if isinstance(detail, dict):
        return detail.get("message") or str(detail)
    return str(detail)[:200]
