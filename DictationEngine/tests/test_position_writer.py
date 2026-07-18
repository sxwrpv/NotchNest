import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.config import write_overlay_position


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_insert_into_existing_overlay_block_preserving_comments(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write(
            "# top comment\n"
            "asr:\n"
            "  model: small.en  # inline comment\n"
            "overlay:\n"
            "  enabled: true\n"
            "  width: 400\n"
            "ui:\n"
            "  show_notifications: true\n"
        )
    assert write_overlay_position(p, 500.25, 42.7)
    data = _load(p)
    assert data["overlay"]["position"] == {"x": 500.2, "y": 42.7}
    assert data["overlay"]["enabled"] is True  # siblings intact
    assert data["overlay"]["width"] == 400
    assert data["ui"]["show_notifications"] is True
    text = open(p).read()
    assert "# top comment" in text  # comments preserved
    assert "# inline comment" in text


def test_replace_existing_flow_position(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("overlay:\n  position: {x: 1, y: 2}\n  width: 300\n")
    assert write_overlay_position(p, 800, 100)
    data = _load(p)
    assert data["overlay"]["position"] == {"x": 800.0, "y": 100.0}
    assert data["overlay"]["width"] == 300


def test_replace_existing_block_position(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("overlay:\n  position:\n    x: 1\n    y: 2\n  width: 300\n")
    assert write_overlay_position(p, 640, 24)
    data = _load(p)
    assert data["overlay"]["position"] == {"x": 640.0, "y": 24.0}
    assert data["overlay"]["width"] == 300


def test_no_overlay_key_appends(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("asr:\n  model: base.en\n")
    assert write_overlay_position(p, 10, 20)
    data = _load(p)
    assert data["overlay"]["position"] == {"x": 10.0, "y": 20.0}
    assert data["asr"]["model"] == "base.en"


def test_inline_flow_overlay(tmp_path):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("overlay: {enabled: true, width: 350}\n")
    assert write_overlay_position(p, 5, 6)
    data = _load(p)
    assert data["overlay"]["position"] == {"x": 5.0, "y": 6.0}
    assert data["overlay"]["enabled"] is True
    assert data["overlay"]["width"] == 350


def test_default_template_roundtrip(tmp_path):
    """The shipped config.default.yaml must survive a position write."""
    import shutil

    src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.default.yaml",
    )
    p = os.path.join(tmp_path, "config.yaml")
    shutil.copyfile(src, p)
    before = _load(p)
    assert write_overlay_position(p, 855.0, 24.0)
    after = _load(p)
    assert after["overlay"]["position"] == {"x": 855.0, "y": 24.0}
    # everything else unchanged
    before["overlay"].pop("position", None)
    after["overlay"].pop("position", None)
    assert before == after
