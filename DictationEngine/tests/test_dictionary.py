import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.config import Config
from murmur.dictionary import PersonalDictionary


def _cfg(tmp_path, overrides=None):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        f.write("dictionary:\n  entries: [PolyCopy]\n  replacements: {}\n")
    return Config(path)


def test_manual_terms_and_prompt(tmp_path):
    cfg = _cfg(tmp_path)
    db = os.path.join(tmp_path, "m.db")
    d = PersonalDictionary(cfg, db)
    assert "PolyCopy" in d.manual_terms()
    assert "PolyCopy" in d.initial_prompt()


def test_auto_learn_from_correction(tmp_path):
    cfg = _cfg(tmp_path)
    db = os.path.join(tmp_path, "m.db")
    d = PersonalDictionary(cfg, db)
    learned = d.record_correction(
        "send it to polycopy team", "send it to PolyCopy team"
    )
    assert any(src.lower() == "polycopy" for src, dst in learned)
    replaced = d.apply_replacements("email the polycopy team today")
    assert "PolyCopy" in replaced


def test_replacement_is_whole_word(tmp_path):
    cfg = _cfg(tmp_path)
    db = os.path.join(tmp_path, "m.db")
    d = PersonalDictionary(cfg, db)
    d.record_correction("ping arsen now", "ping Arsen now")
    out = d.apply_replacements("arsenal beat arsen to it")
    assert "Arsen " in out or out.endswith("Arsen")
    assert "Arsenal" not in out  # whole-word match must not clobber "arsenal"
