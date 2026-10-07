import json

import pytest

from chesscoach.coach import CoachError, build_ask_context, build_prompt, ollama_num_ctx, resolve


@pytest.fixture(autouse=True)
def no_llm_env(monkeypatch):
    for var in ("CHESSCOACH_LLM_PROVIDER", "CHESSCOACH_LLM_MODEL", "CHESSCOACH_LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_resolve_defaults_to_anthropic():
    assert resolve(None, None, None) == ("anthropic", "claude-opus-5-5", None)


def test_resolve_fills_provider_base_url():
    assert resolve("ollama", "llama3.1", None) == ("ollama", "llama3.1", "http://localhost:11434")
    assert resolve("groq", "m", None)[2] == "https://api.groq.com/openai/v1"
    assert resolve("openai", "m", None)[2] is None  # SDK default
    assert resolve("openai", "m", "http://proxy")[2] == "http://proxy"


def test_resolve_reads_env(monkeypatch):
    monkeypatch.setenv("CHESSCOACH_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("CHESSCOACH_LLM_MODEL", "some-model")
    assert resolve(None, None, None)[:2] == ("gemini", "some-model")


@pytest.mark.parametrize("args", [
    ("nope", "m", None),        # unknown provider
    ("openai", None, None),     # non-anthropic needs a model
    ("custom", "m", None),      # custom needs a base URL
])
def test_resolve_rejects_bad_config(args):
    with pytest.raises(CoachError):
        resolve(*args)


def test_build_prompt_trims_openings():
    report = {"overview": {"games": 1}, "openings": [{"opening": str(i)} for i in range(20)]}
    prompt = build_prompt(report, top_openings=3)
    data = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
    assert [o["opening"] for o in data["openings"]] == ["0", "1", "2"]
    assert len(report["openings"]) == 20  # input not mutated


def test_build_ask_context_has_report_and_csv_table():
    report = {"overview": {"games": 2}, "openings": [{"opening": str(i)} for i in range(20)]}
    games = [{"url": "u/a", "opening": "Sicilian, Najdorf", "acpl": None},
             {"url": "u/b", "opening": 'The "Fried Liver"', "acpl": 31.5}]
    ctx = build_ask_context(report, games, top_openings=2)
    data = json.loads(ctx.split("```json\n")[1].split("\n```")[0])
    assert len(data["openings"]) == 2
    table = ctx.split("```csv\n")[1].split("\n```")[0].splitlines()
    assert table == ["url,opening,acpl", 'u/a,"Sicilian, Najdorf",', 'u/b,"The ""Fried Liver""",31.5']


def test_ollama_num_ctx_grows_to_fit_in_powers_of_two():
    assert ollama_num_ctx("s", [{"role": "user", "content": "hi"}]) == 16384
    big = [{"role": "user", "content": "x" * 3 * 20000}]  # ~20k tokens + reply room
    assert ollama_num_ctx("", big) == 32768
