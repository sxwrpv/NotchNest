import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.config import Config


def test_defaults_merge(tmp_path):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        f.write("asr:\n  model: base.en\n")
    cfg = Config(path)
    assert cfg.get("asr.model") == "base.en"
    assert cfg.get("asr.language") == "auto"  # default preserved
    assert cfg.get("hotkeys.dictation_key") == "fn"


def test_hot_reload(tmp_path):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        f.write("asr:\n  model: base.en\n")
    cfg = Config(path)
    seen = []
    cfg.on_reload(lambda c: seen.append(c.get("asr.model")))
    cfg.start_watching(interval_s=0.1)
    time.sleep(0.15)
    with open(path, "w") as f:
        f.write("asr:\n  model: small.en\n")
    time.sleep(0.4)
    cfg.stop_watching()
    assert cfg.get("asr.model") == "small.en"
    assert "small.en" in seen


def test_bad_yaml_keeps_previous(tmp_path):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        f.write("asr:\n  model: base.en\n")
    cfg = Config(path)
    with open(path, "w") as f:
        f.write("asr:\n  model: [broken\n")
    ok = cfg.load()
    assert ok is False
    assert cfg.get("asr.model") == "base.en"
