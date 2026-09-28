import json
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import setup_engine
from murmur import paths


def test_ram_tiers_pick_the_cleanup_model():
    # Awkward values on purpose: the 12 GB boundary and non-round sizes.
    assert setup_engine.hardware_profile(8.0)["llm.mlx.model"] == setup_engine.LLM_SMALL
    assert setup_engine.hardware_profile(11.9)["llm.mlx.model"] == setup_engine.LLM_SMALL
    assert setup_engine.hardware_profile(12.0)["llm.mlx.model"] == setup_engine.LLM_LARGE
    assert setup_engine.hardware_profile(24.0)["llm.mlx.model"] == setup_engine.LLM_LARGE


def _point_paths_at(tmp_path, monkeypatch):
    config_dir = tmp_path / ".murmur"
    monkeypatch.setattr(paths, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(paths, "CONFIG_PATH", str(config_dir / "config.yaml"))
    return config_dir / "config.yaml"


def test_configure_tunes_a_fresh_config(tmp_path, monkeypatch, capsys):
    config = _point_paths_at(tmp_path, monkeypatch)
    monkeypatch.setattr(setup_engine, "ram_gb", lambda: 8.0)

    setup_engine.cmd_configure()

    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "configured" and event["fresh"] is True
    data = yaml.safe_load(config.read_text())
    assert data["llm"]["mlx"]["model"] == setup_engine.LLM_SMALL
    assert data["hotkeys"]["dictation_key"] == "right_option"
    assert data["overlay"]["enabled"] is False
    assert "# It hot-reloads on save" in config.read_text()  # template comments kept


def test_configure_never_touches_an_existing_config(tmp_path, monkeypatch, capsys):
    config = _point_paths_at(tmp_path, monkeypatch)
    config.parent.mkdir()
    original = "asr:\n  model: small.en\nllm:\n  cleanup_enabled: false\n"
    config.write_text(original)
    monkeypatch.setattr(setup_engine, "ram_gb", lambda: 24.0)

    setup_engine.cmd_configure()

    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["fresh"] is False
    assert config.read_text() == original


def test_models_skip_the_llm_when_cleanup_is_off(tmp_path, monkeypatch):
    config = _point_paths_at(tmp_path, monkeypatch)
    config.parent.mkdir()
    config.write_text("asr:\n  model: small.en\nllm:\n  cleanup_enabled: false\n")

    assert setup_engine.wanted_models() == [
        ("Speech model", "mlx-community/whisper-small.en-mlx")
    ]
