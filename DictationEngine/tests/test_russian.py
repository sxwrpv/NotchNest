"""Cyrillic/Russian support: filler stripping, capitalization, spoken
punctuation, dictionary word boundaries, and snippet triggers."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.cleanup import rule_based_cleanup
from murmur.config import Config
from murmur.dictionary import PersonalDictionary
from murmur.snippets import Snippets
from murmur.transcriber import resolve_model


def _cfg(tmp_path, text):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return Config(path)


def test_russian_fillers_stripped():
    out = rule_based_cleanup("э-э привет как бы это тест эм ладно")
    low = out.lower()
    assert "э-э" not in low and "эм" not in low
    assert "как бы" not in low
    assert "привет" in low and "тест" in low


def test_russian_capitalization_and_terminal_punct():
    out = rule_based_cleanup("привет мир")
    assert out.startswith("П"), out
    assert out.endswith(".")


def test_russian_spoken_punctuation():
    out = rule_based_cleanup("привет запятая как дела вопросительный знак")
    assert "," in out
    assert out.strip().endswith("?")


def test_context_dependent_russian_words_not_stripped():
    # "типа"/"короче" are real words in many sentences; the deterministic
    # fallback must NOT strip them (the LLM handles them with context).
    out = rule_based_cleanup("короче на два метра и типа данных")
    assert "короче" in out.lower()
    assert "типа" in out.lower()


def test_cyrillic_dictionary_replacement(tmp_path):
    cfg = _cfg(
        tmp_path,
        'dictionary:\n  entries: []\n  replacements:\n    "полипай": "PolyPy"\n',
    )
    d = PersonalDictionary(cfg, os.path.join(tmp_path, "m.db"))
    out = d.apply_replacements("запусти полипай сегодня")
    assert "PolyPy" in out
    # word-boundary check: substrings inside longer Cyrillic words survive
    out2 = d.apply_replacements("полипайщик не должен меняться")
    assert "полипайщик" in out2


def test_cyrillic_correction_learning(tmp_path):
    cfg = _cfg(tmp_path, "dictionary:\n  auto_learn: true\n")
    d = PersonalDictionary(cfg, os.path.join(tmp_path, "m.db"))
    learned = d.record_correction("привет мурмур", "привет Murmur")
    assert any(s.lower() == "мурмур" for s, _ in learned)
    assert "Murmur" in d.apply_replacements("открой мурмур")


def test_cyrillic_snippet_trigger(tmp_path):
    cfg = _cfg(
        tmp_path,
        'snippets:\n  "вставь мою подпись": "С уважением,\\nАрсен"\n',
    )
    s = Snippets(cfg)
    assert s.match("вставь мою подпись") == "С уважением,\nАрсен"
    assert s.match("Вставь мою подпись.") == "С уважением,\nАрсен"
    assert s.match("что-то совсем другое") is None


def test_default_model_is_multilingual_turbo():
    assert resolve_model("large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"
    cfg_default = Config.__module__  # noqa: F841
    from murmur.config import DEFAULTS

    assert DEFAULTS["asr"]["model"] == "large-v3-turbo-q4"
    assert DEFAULTS["asr"]["language"] == "auto"
