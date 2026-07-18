"""Generic comment-preserving config writer used by the Settings GUI."""

import os
import shutil
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.config import write_config_values

TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.default.yaml"
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_scalar_update_preserves_comments(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("# header\nasr:\n  model: small.en  # note\n  language: en\n")
    assert write_config_values(p, {"asr.model": "large-v3-turbo"})
    data = _load(p)
    assert data["asr"]["model"] == "large-v3-turbo"
    assert data["asr"]["language"] == "en"
    text = open(p).read()
    assert "# header" in text


def test_multiple_updates_and_deep_key(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("llm:\n  backend: auto\n  ollama:\n    model: qwen2.5:3b-instruct\n")
    ok = write_config_values(
        p, {"llm.backend": "mlx", "llm.ollama.model": "llama3.2:3b", "llm.mlx.idle_unload_s": 60}
    )
    assert ok
    data = _load(p)
    assert data["llm"]["backend"] == "mlx"
    assert data["llm"]["ollama"]["model"] == "llama3.2:3b"
    assert data["llm"]["mlx"]["idle_unload_s"] == 60  # created missing subtree


def test_replace_dict_subtree(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write('snippets:\n  "old trigger": "old text"\nasr:\n  model: small.en\n')
    snippets = {"вставь подпись": "С уважением,\nАрсен", "sig": "Best"}
    assert write_config_values(p, {"snippets": snippets})
    data = _load(p)
    assert data["snippets"] == snippets
    assert data["asr"]["model"] == "small.en"


def test_replace_list_and_empty_containers(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("dictionary:\n  entries:\n    - OldTerm\n  auto_learn: true\n")
    assert write_config_values(p, {"dictionary.entries": ["PolyCopy", "Мурмур"]})
    data = _load(p)
    assert data["dictionary"]["entries"] == ["PolyCopy", "Мурмур"]
    assert data["dictionary"]["auto_learn"] is True
    assert write_config_values(p, {"dictionary.entries": []})
    assert _load(p)["dictionary"]["entries"] == []


def test_missing_top_level_section_appended(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("asr:\n  model: base.en\n")
    assert write_config_values(p, {"styles.per_app": {"com.apple.mail": "formal"}})
    data = _load(p)
    assert data["styles"]["per_app"] == {"com.apple.mail": "formal"}


def test_null_value(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("overlay:\n  position: {x: 1.0, y: 2.0}\n  width: 400\n")
    assert write_config_values(p, {"overlay.position": None})
    data = _load(p)
    assert data["overlay"]["position"] is None
    assert data["overlay"]["width"] == 400


def test_full_template_roundtrip_many_keys(tmp_path):
    p = os.path.join(tmp_path, "config.yaml")
    shutil.copyfile(TEMPLATE, p)
    before = _load(p)
    updates = {
        "hotkeys.dictation_key": "right_command",
        "asr.language": "ru",
        "insertion.keep_on_clipboard": True,
        "styles.default": "formal",
        "logging.level": "DEBUG",
    }
    assert write_config_values(p, updates)
    after = _load(p)
    assert after["hotkeys"]["dictation_key"] == "right_command"
    assert after["asr"]["language"] == "ru"
    assert after["insertion"]["keep_on_clipboard"] is True
    assert after["styles"]["default"] == "formal"
    assert after["logging"]["level"] == "DEBUG"
    # untouched keys identical
    assert after["audio"] == before["audio"]
    assert after["llm"] == before["llm"]
    assert after["snippets"] == before["snippets"]
