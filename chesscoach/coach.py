"""LLM coaching layer: turns build_report() output into a plain-language training plan.

Provider-agnostic. Anthropic uses its own SDK; Ollama uses its native API (its
OpenAI-compatible endpoint ignores context size, and the 4096-token default is too small);
everything else (OpenAI, Gemini, OpenRouter, Groq, ...) goes through the OpenAI SDK pointed at
that provider's OpenAI-compatible endpoint. SDKs are imported lazily so only the one in use
must be installed.
"""
import json
import os
from collections.abc import Iterator

TOP_OPENINGS = 8
OLLAMA_NUM_CTX = 16384  # minimum; grown to fit long chats (see ollama_num_ctx)
OLLAMA_REPLY_TOKENS = 8192  # room for reasoning + answer

# OpenAI-compatible providers: base URL and the env var holding the API key (None = no key).
OPENAI_COMPATIBLE = {
    "openai": (None, "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
}
OLLAMA_BASE_URL = "http://localhost:11434"
PROVIDERS = ("anthropic", "ollama", *OPENAI_COMPATIBLE, "custom")

DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
# Claude models that accept output_config.effort and server-side refusal fallbacks.
CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

# How build_report's fields were computed; shared by the plan and ask prompts.
REPORT_NOTES = """\
How the data was computed:
- Moves are classified by how much they drop the student's win chance (Lichess win% curve): \
>=5 points inaccuracy, >=10 mistake, >=15 blunder. ACPL = average centipawn loss.
- Phases: endgame when combined non-pawn material <= 26, opening through move 12, \
middlegame otherwise.
- "openings" is pre-sorted by "priority" (loss rate + how early and often the first mistake \
or blunder lands). "median_first_error_move" is the typical move of the first big error; \
an early number means the student leaves theory or misplays the opening itself.
- "time_trouble" compares blunder rates with under N seconds on the clock vs. otherwise.
- "worst_moments" are the biggest single blunders: the move played ("san"), the engine's \
best move ("best_san"), the position before the move ("fen_before"), and the win% lost. \
Refer to moves by "move_number" (e.g. "move 26"), never by ply, and include the game "url".
- "motifs" tags tactical patterns, each with a count, rate per 100 games, and its single worst \
example. missed_mate_in_N: the student had a forced mate in N (1-3) and played a move that gave \
it up; allowed_mate_in_N: the student's move let the opponent force mate in N; back_rank: that \
mate ends on the student's back rank with the king boxed in by its own pieces. These mate tags \
apply to any move. Only on mistakes/blunders: hung_piece (the move let the opponent win a \
piece), allowed_fork, missed_capture (the best move won a piece), missed_fork. Many errors are \
untagged (positional or deeper tactics), so treat counts as lower bounds. Judge motifs by how \
often they happen ("count", "per_100_games") first; a rare motif with a big average drop \
matters less.
"""

SYSTEM_PROMPT = """\
You are an experienced chess coach reviewing a student's games. You receive a JSON report \
produced by running Stockfish over the student's recent online games. Write a personalized \
training plan from it.

""" + REPORT_NOTES + """
Rules:
- Ground every claim in the numbers provided and cite them. Do not invent statistics.
- You cannot see the games beyond this data. Don't make up concrete variations; for specific \
positions, rely only on the played move vs. the engine's best move, and say what kind of \
oversight it suggests only when that is clear from those two moves.
- Prioritize: say what will gain the student the most rating points first.

Format (Markdown, roughly 400-700 words):
1. **Big picture** - 2-3 sentences on the student's profile.
2. **Top 3 priorities** - each with the evidence and a concrete weekly practice task.
3. **Openings to fix** - the most urgent ones, using priority and first-error move.
4. **Positions to review** - a few of the worst moments, with the game link, what was played \
and what the engine preferred.
"""

ASK_SYSTEM_PROMPT = """\
You are an experienced chess coach answering a student's questions about their own games. \
The first message holds a JSON report from running Stockfish over the student's recent online \
games, plus a CSV table with one row per game. Answer the questions that follow.

""" + REPORT_NOTES + """
The games table (oldest first) has one row per game, from the student's side:
- date, weekday, hour: local time the game started.
- session_game: the game's position in a playing session (1 = first game after a break of 30+ \
minutes, 2 = the next game, ...). Use it with result to judge tilt and fatigue.
- time_class (bullet/blitz/rapid/daily...), color, user_rating, opponent_rating, result \
(win/draw/loss), opening, moves (game length).
- acpl, and counts of inaccuracies / mistakes / blunders the student made in that game.
- first_error_move: move number of the student's first mistake or blunder (empty = none).
- min_clock: lowest clock the student had, in seconds; low_clock_blunders: blunders with \
under 30 seconds left. Both empty when the site gave no clock data.
- url: link to the game; include it when pointing at a specific game.

Rules:
- Answer the question asked, directly, then show the evidence. Compute from the table \
(group, count, compare rates) and give the numbers and sample sizes behind each claim.
- Say when a sample is too small to conclude anything (fewer than ~10 games in a group). When \
the data can't answer a question, say so and what data would be needed.
- Do not invent statistics or concrete variations. You only know the data above.
- End with one concrete practice suggestion when it follows from the answer.
- Keep answers focused: usually 100-350 words of Markdown, more only if asked.
"""


class CoachError(Exception):
    """A user-facing problem (missing key, unreachable server, refusal)."""


def build_prompt(report: dict, top_openings: int = TOP_OPENINGS) -> str:
    """The user message: the report trimmed to what the coach needs."""
    data = {**report, "openings": report["openings"][:top_openings]}
    # Compact JSON: ~25% fewer tokens than indented, which matters for small local contexts.
    return ("Here is my analysis report. Please coach me.\n\n"
            f"```json\n{json.dumps(data, separators=(',', ':'))}\n```")


def resolve(provider: str | None, model: str | None, base_url: str | None) -> tuple[str, str, str | None]:
    """Fill provider/model/base_url from args, then env, then defaults; validate."""
    provider = provider or os.environ.get("CHESSCOACH_LLM_PROVIDER") or DEFAULT_PROVIDER
    model = model or os.environ.get("CHESSCOACH_LLM_MODEL")
    base_url = base_url or os.environ.get("CHESSCOACH_LLM_BASE_URL")
    if provider not in PROVIDERS:
        raise CoachError(f"Unknown provider {provider!r}. Choose from: {', '.join(PROVIDERS)}.")
    if provider == "anthropic":
        model = model or DEFAULT_ANTHROPIC_MODEL
    elif not model:
        raise CoachError(f"Pass --model (or set CHESSCOACH_LLM_MODEL) for provider {provider!r}.")
    if provider == "custom" and not base_url:
        raise CoachError("Provider 'custom' needs --base-url (or CHESSCOACH_LLM_BASE_URL).")
    if not base_url:
        base_url = OLLAMA_BASE_URL if provider == "ollama" else OPENAI_COMPATIBLE.get(provider, (None,))[0]
    return provider, model, base_url


def build_ask_context(report: dict, games: list[dict], top_openings: int = TOP_OPENINGS) -> str:
    """The first user message of an `ask` chat: the report plus one CSV row per game."""
    data = {**report, "openings": report["openings"][:top_openings]}
    cols = list(games[0]) if games else []
    # CSV, not JSON: column names once instead of per game roughly halves the tokens.
    table = "\n".join([",".join(cols), *(",".join(_csv_cell(g[c]) for c in cols) for g in games)])
    return ("Here is my analysis report and a table of my recent games. I'll ask questions "
            "about them.\n\n"
            f"```json\n{json.dumps(data, separators=(',', ':'))}\n```\n\n"
            f"Games ({len(games)}, oldest first):\n```csv\n{table}\n```")


def _csv_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\n') else text


def stream_coaching(report: dict, provider: str | None = None, model: str | None = None,
                    base_url: str | None = None) -> Iterator[str]:
    """Yield the coaching text as it streams in."""
    messages = [{"role": "user", "content": build_prompt(report)}]
    yield from stream_chat(SYSTEM_PROMPT, messages, provider, model, base_url)


def stream_chat(system: str, messages: list[dict], provider: str | None = None,
                model: str | None = None, base_url: str | None = None,
                cache_context: bool = False) -> Iterator[str]:
    """Yield the reply to a conversation of {"role": "user"|"assistant", "content": str}.

    cache_context: the first message will be resent on later turns, so let providers that
    support explicit prompt caching (Anthropic) cache it.
    """
    provider, model, base_url = resolve(provider, model, base_url)
    if provider == "anthropic":
        yield from _stream_anthropic(model, system, messages, cache_context)
    elif provider == "ollama":
        yield from _stream_ollama(model, base_url, system, messages)
    else:
        key_env = OPENAI_COMPATIBLE[provider][1] if provider in OPENAI_COMPATIBLE else "CHESSCOACH_LLM_API_KEY"
        yield from _stream_openai(provider, model, base_url, key_env, system, messages)


def _stream_anthropic(model: str, system: str, messages: list[dict],
                      cache_context: bool = False) -> Iterator[str]:
    try:
        import anthropic
    except ImportError:
        raise CoachError("Install the Anthropic SDK: pip install anthropic") from None

    kwargs = {}
    if model in CLAUDE_FALLBACK_MODELS:
        # If a safety classifier declines, re-run server-side on Anthropic's recommended fallback.
        kwargs = {"output_config": {"effort": "medium"},
                  "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if cache_context:
        first = {"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}
        messages = [{"role": messages[0]["role"], "content": [first]}, *messages[1:]]
    try:
        with anthropic.Anthropic().beta.messages.stream(
            model=model, max_tokens=16000, system=system, messages=messages, **kwargs,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise CoachError("Anthropic rejected the credentials. Set ANTHROPIC_API_KEY.") from None
    except anthropic.NotFoundError:
        raise CoachError(f"Anthropic model {model!r} not found.") from None
    except anthropic.APIConnectionError:
        raise CoachError("Could not reach the Anthropic API. Check your connection.") from None
    except anthropic.APIStatusError as e:
        raise CoachError(f"Anthropic API error {e.status_code}: {e.message}") from None
    if final.stop_reason == "max_tokens" and not any(b.type == "text" for b in final.content):
        raise CoachError(_empty_reply_message(hit_limit=True))
    if final.stop_reason == "refusal":
        raise CoachError("The model declined to answer.")
    if final.stop_reason == "max_tokens":
        yield "\n\n[truncated: hit the output limit]"


def _stream_openai(provider: str, model: str, base_url: str | None, key_env: str | None,
                   system: str, messages: list[dict]) -> Iterator[str]:
    try:
        import openai
    except ImportError:
        raise CoachError("Install the OpenAI SDK (used for all OpenAI-compatible providers): "
                         "pip install openai") from None

    # The SDK insists on some key; local servers (Ollama, LM Studio, ...) ignore it.
    api_key = os.environ.get(key_env) if key_env else None
    if not api_key and provider == "custom":
        api_key = "unused"
    if not api_key:
        raise CoachError(f"Set {key_env} to use provider {provider!r}.")
    try:
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        stream = client.chat.completions.create(
            model=model, stream=True,
            messages=[{"role": "system", "content": system}, *messages],
        )
        wrote, finish = False, None
        for chunk in stream:
            if not chunk.choices:
                continue
            finish = chunk.choices[0].finish_reason or finish
            if chunk.choices[0].delta.content:
                wrote = True
                yield chunk.choices[0].delta.content
    except openai.AuthenticationError:
        raise CoachError(f"{provider} rejected the API key in {key_env}.") from None
    except openai.NotFoundError:
        raise CoachError(f"{provider} has no model {model!r}.") from None
    except openai.APIConnectionError:
        raise CoachError(f"Could not reach {base_url or provider}.") from None
    except openai.APIStatusError as e:
        raise CoachError(f"{provider} API error {e.status_code}: {e.message}") from None
    if not wrote:
        raise CoachError(_empty_reply_message(finish == "length"))


def ollama_num_ctx(system: str, messages: list[dict]) -> int:
    """A context window big enough for the prompt (~3 chars/token for this JSON/CSV-heavy text)
    plus room to answer, in powers of two from OLLAMA_NUM_CTX; bigger costs RAM, so no more."""
    needed = (len(system) + sum(len(m["content"]) for m in messages)) // 3 + OLLAMA_REPLY_TOKENS
    num_ctx = OLLAMA_NUM_CTX
    while num_ctx < needed:
        num_ctx *= 2
    return num_ctx


def _stream_ollama(model: str, base_url: str, system: str, messages: list[dict]) -> Iterator[str]:
    import requests

    try:
        resp = requests.post(f"{base_url.rstrip('/')}/api/chat", stream=True, timeout=(10, 600), json={
            "model": model, "stream": True, "options": {"num_ctx": ollama_num_ctx(system, messages)},
            "messages": [{"role": "system", "content": system}, *messages],
        })
    except requests.ConnectionError:
        raise CoachError(f"Could not reach Ollama at {base_url}. Is `ollama serve` running?") from None
    if resp.status_code == 404:
        raise CoachError(f"Ollama has no model {model!r}. Try `ollama pull {model}`.")
    if not resp.ok:
        raise CoachError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

    wrote, done_reason = False, None
    for line in resp.iter_lines():
        if not line:
            continue
        msg = json.loads(line)
        if "error" in msg:
            raise CoachError(f"Ollama error: {msg['error']}")
        text = msg.get("message", {}).get("content")
        if text:
            wrote = True
            yield text
        done_reason = msg.get("done_reason") or done_reason
    if not wrote:
        raise CoachError(_empty_reply_message(done_reason == "length"))


def _empty_reply_message(hit_limit: bool) -> str:
    if hit_limit:
        return ("The model hit its token/context limit before writing an answer (reasoning models "
                "can spend it all thinking). Try a larger-context or non-reasoning model.")
    return "The model returned an empty answer."
