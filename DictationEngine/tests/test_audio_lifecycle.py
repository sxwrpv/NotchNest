"""The microphone stream is open only while dictating unless always_on_capture
asks for pre-roll: an open stream keeps macOS's mic indicator lit."""

import os
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.audio import AudioCapture
from murmur.config import Config


class FakeStream:
    opened = []

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.active = False
        FakeStream.opened.append(self)

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False


@pytest.fixture(autouse=True)
def fake_sounddevice(monkeypatch):
    FakeStream.opened = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=FakeStream))


def _capture(tmp_path, always_on=None):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("" if always_on is None else f"audio:\n  always_on_capture: {str(always_on).lower()}\n")
    return AudioCapture(Config(p))


def _mic_open(capture):
    return capture._stream is not None and capture._stream.active


def _feed(capture, seconds=0.5):
    block = np.full((480, 1), 0.1, dtype=np.float32)
    for _ in range(int(seconds / 0.03)):
        capture._stream.callback(block, 480, None, None)


def test_default_opens_the_mic_only_while_recording(tmp_path):
    capture = _capture(tmp_path)  # no key: the built-in default applies
    capture.start_if_always_on()
    assert not _mic_open(capture)

    assert capture.start_recording()
    assert _mic_open(capture)
    _feed(capture)
    samples = capture.stop_recording()
    assert len(samples) > 0
    assert not _mic_open(capture)


def test_canceling_closes_the_mic(tmp_path):
    capture = _capture(tmp_path, always_on=False)
    capture.start_recording()
    capture.abort_recording()
    assert not _mic_open(capture)


def test_always_on_keeps_the_mic_open_for_preroll(tmp_path):
    capture = _capture(tmp_path, always_on=True)
    capture.start_if_always_on()
    assert _mic_open(capture)
    _feed(capture, 0.3)  # pre-roll lands in the ring buffer before the hotkey
    capture.start_recording()
    assert len(capture._chunks) > 0
    capture.stop_recording()
    capture.abort_recording()
    assert _mic_open(capture)
    assert len(FakeStream.opened) == 1


def test_switching_always_on_off_releases_an_idle_mic(tmp_path):
    capture = _capture(tmp_path, always_on=True)
    capture.start_if_always_on()
    capture.config._data["audio"]["always_on_capture"] = False
    capture.release_if_idle()
    assert not _mic_open(capture)


def test_release_never_cuts_a_recording_short(tmp_path):
    capture = _capture(tmp_path, always_on=False)
    capture.start_recording()
    capture.release_if_idle()
    assert _mic_open(capture)


def test_template_defaults_to_mic_only_while_dictating():
    import yaml

    template = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config.default.yaml")
    with open(template) as f:
        assert yaml.safe_load(f)["audio"]["always_on_capture"] is False
