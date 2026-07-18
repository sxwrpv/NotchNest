import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.config import Config
from murmur.snippets import Snippets
from murmur.styles import StyleManager


def _cfg(tmp_path, text):
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        f.write(text)
    return Config(path)


def test_snippet_exact_and_fuzzy_match(tmp_path):
    cfg = _cfg(
        tmp_path,
        'snippets:\n  "insert my signature": "Best,\\nArsen"\n',
    )
    s = Snippets(cfg)
    assert s.match("insert my signature") == "Best,\nArsen"
    assert s.match("please insert my signature") == "Best,\nArsen"
    assert s.match("what is the weather today") is None


def test_style_per_app_default(tmp_path):
    cfg = _cfg(
        tmp_path,
        "styles:\n  default: neutral\n  per_app:\n    com.apple.mail: formal\n",
    )
    sm = StyleManager(cfg)
    name, _ = sm.resolve("com.apple.mail")
    assert name == "formal"
    name2, _ = sm.resolve("com.tinyspeck.slackmacgap")
    assert name2 == "neutral"


def test_style_override_beats_per_app(tmp_path):
    cfg = _cfg(
        tmp_path,
        "styles:\n  default: neutral\n  per_app:\n    com.apple.mail: formal\n",
    )
    sm = StyleManager(cfg)
    name, _ = sm.resolve("com.apple.mail", override="casual")
    assert name == "casual"
    # None override falls back to per-app
    name2, _ = sm.resolve("com.apple.mail", override=None)
    assert name2 == "formal"


def test_style_cycle_order(tmp_path):
    cfg = _cfg(
        tmp_path,
        'styles:\n  presets:\n    pirate: "Arr."\n',
    )
    sm = StyleManager(cfg)
    order = sm.cycle_order()
    assert order[0] is None  # Auto first
    assert "neutral" in order and "formal" in order and "casual" in order
    assert "pirate" in order
    # a full cycle returns to Auto
    cur = None
    for _ in range(len(order)):
        cur = sm.next_in_cycle(cur)
    assert cur is None
