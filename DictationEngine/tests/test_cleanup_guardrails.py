"""Guardrails against the LLM answering conversationally instead of cleaning
the transcript (seen live: ASR 'Tanigo' -> 'It seems there might be a typo.
Could you please provide the correct text?')."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.cleanup import CleanupEngine, _sane
from murmur.config import Config


class _StubRouter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply, "stub"


def _engine(tmp_path, reply):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("llm:\n  cleanup_enabled: true\n")
    return CleanupEngine(Config(p), _StubRouter(reply))


# ---- the live bug ----------------------------------------------------------
def test_meta_chatter_rejected_falls_back_to_raw(tmp_path):
    eng = _engine(
        tmp_path, "It seems there might be a typo. Could you please provide the correct text?"
    )
    out, engine = eng.clean("Tanigo system check running")
    assert engine == "rules-guard"
    assert "typo" not in out.lower()
    assert "Tanigo" in out


def test_short_utterance_skips_llm_entirely(tmp_path):
    eng = _engine(tmp_path, "SHOULD NEVER BE USED")
    out, engine = eng.clean("Tanigo")
    assert engine == "rules-short"
    assert "Tanigo" in out
    assert eng.router.calls == 0  # LLM not even consulted


def test_english_reply_to_russian_input_rejected(tmp_path):
    eng = _engine(tmp_path, "I'm not sure what this Russian text means, sorry!")
    out, engine = eng.clean("проверка системы диктовки работает нормально")
    assert engine == "rules-guard"
    assert "проверка" in out.lower()


def test_low_word_overlap_rejected(tmp_path):
    # polite meta-answer with no forbidden marker still gets caught by overlap
    eng = _engine(tmp_path, "The weather in Paris is lovely this time of year indeed.")
    out, engine = eng.clean("send the quarterly report to the finance team tomorrow")
    assert engine == "rules-guard"
    assert "report" in out.lower()


def test_legitimate_cleanup_accepted(tmp_path):
    eng = _engine(tmp_path, "Hello, how are you doing today?")
    out, engine = eng.clean("um hello uh how are you doing today")
    assert engine == "stub"
    assert out == "Hello, how are you doing today?"


def test_marker_in_input_is_allowed_through(tmp_path):
    eng = _engine(tmp_path, "I fixed a typo in the readme yesterday evening.")
    out, engine = eng.clean("um i fixed a typo in the readme yesterday evening")
    assert engine == "stub"  # "typo" came from the speaker, not the model


def test_collapsed_output_rejected(tmp_path):
    eng = _engine(tmp_path, "Okay.")
    out, engine = eng.clean(
        "please write down the following list of fourteen different grocery items "
        "we need for the party on saturday including drinks and desserts"
    )
    assert engine == "rules-guard"


# ---- direct _sane unit checks ------------------------------------------------
def test_sane_direct():
    assert _sane("hello world how are you", "Hello world, how are you?")
    assert not _sane("тест системы", "Please provide the correct text.")
    assert not _sane("hello there my friend", "")
