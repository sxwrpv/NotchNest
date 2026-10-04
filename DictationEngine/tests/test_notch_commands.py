"""notch.cmd authentication: only commands NotchNest signed with the secret it
handed this engine on stdin may drive the microphone or the config. Anything
else running as the user can write (and read) ~/.murmur/notch.cmd."""

import hashlib
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur import notch_bridge
from murmur.notch_bridge import NotchBridge, parse_secret, read_secret, sign_command

SECRET = hashlib.sha256(b"notchnest test secret").digest()
OTHER = hashlib.sha256(b"some other launch").digest()


class FakeController:
    def __init__(self):
        self.calls = []

    def toggle_dictation(self):
        self.calls.append("toggle")

    def ptt_start(self):
        self.calls.append("start")

    def ptt_stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")


@pytest.fixture
def cmd_path(tmp_path, monkeypatch):
    path = str(tmp_path / "notch.cmd")
    monkeypatch.setattr(notch_bridge, "CMD_PATH", path)
    return path


def _bridge(secret=SECRET):
    controller = FakeController()
    return NotchBridge(controller, secret=secret), controller


def _send(cmd_path, bridge, line):
    with open(cmd_path, "w", encoding="utf-8") as fh:
        fh.write(line)
    bridge._pump_commands()


def test_signed_command_runs_and_the_file_is_removed(cmd_path):
    bridge, controller = _bridge()
    _send(cmd_path, bridge, sign_command(SECRET, 1, "start"))
    assert controller.calls == ["start"]
    assert not os.path.lexists(cmd_path)

    _send(cmd_path, bridge, sign_command(SECRET, 2, "stop"))
    assert controller.calls == ["start", "stop"]
    assert not os.path.lexists(cmd_path)


def test_unsigned_command_is_ignored_removed_and_not_echoed(cmd_path, caplog):
    bridge, controller = _bridge()
    with caplog.at_level(logging.WARNING, logger="murmur.notch_bridge"):
        _send(cmd_path, bridge, "start")
        _send(cmd_path, bridge, 'set asr.language "xx-planted"')
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)
    assert len(caplog.records) == 2
    assert "not signed" in caplog.text
    assert "xx-planted" not in caplog.text


def test_command_signed_with_another_secret_is_ignored(cmd_path, caplog):
    bridge, controller = _bridge()
    with caplog.at_level(logging.WARNING, logger="murmur.notch_bridge"):
        _send(cmd_path, bridge, sign_command(OTHER, 1, "start"))
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)
    assert "wrong signature" in caplog.text


def test_the_secret_itself_is_not_a_password(cmd_path):
    # Putting the secret in the file would hand it to every reader of it.
    bridge, controller = _bridge()
    _send(cmd_path, bridge, f"{SECRET.hex()} start")
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)


def test_seen_command_cannot_be_replayed(cmd_path, caplog):
    bridge, controller = _bridge()
    line = sign_command(SECRET, 7, "toggle")
    _send(cmd_path, bridge, line)
    with caplog.at_level(logging.WARNING, logger="murmur.notch_bridge"):
        _send(cmd_path, bridge, line)
    assert controller.calls == ["toggle"]
    assert not os.path.lexists(cmd_path)
    assert "replayed" in caplog.text


def test_counter_must_grow_but_may_skip(cmd_path):
    # NotchNest can overwrite a command the engine never read, so gaps are fine.
    bridge, controller = _bridge()
    _send(cmd_path, bridge, sign_command(SECRET, 5, "start"))
    _send(cmd_path, bridge, sign_command(SECRET, 3, "cancel"))
    _send(cmd_path, bridge, sign_command(SECRET, 9, "stop"))
    assert controller.calls == ["start", "stop"]


def test_seen_signature_cannot_be_moved_to_another_command(cmd_path):
    bridge, controller = _bridge()
    counter, mac, _ = sign_command(SECRET, 1, "cancel").split(" ", 2)
    _send(cmd_path, bridge, f"{counter} {mac} start")
    _send(cmd_path, bridge, f"2 {mac} cancel")
    assert controller.calls == []


def test_no_secret_means_no_commands(cmd_path, caplog):
    # A plain `python main.py` run: state is still published, notch.cmd ignored.
    bridge, controller = _bridge(secret=None)
    with caplog.at_level(logging.WARNING, logger="murmur.notch_bridge"):
        _send(cmd_path, bridge, sign_command(SECRET, 1, "start"))
        _send(cmd_path, bridge, "start")
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)
    assert "disabled" in caplog.text


def test_set_needs_a_signature_too(cmd_path, monkeypatch):
    import murmur.config

    writes = []
    monkeypatch.setattr(murmur.config, "write_config_values",
                        lambda path, values: writes.append(values) or True)
    bridge, _ = _bridge()
    _send(cmd_path, bridge, 'set asr.language "de"')
    _send(cmd_path, bridge, sign_command(SECRET, 1, 'set asr.language "ru"'))
    _send(cmd_path, bridge, sign_command(SECRET, 2, 'set logging.level "DEBUG"'))
    assert writes == [{"asr.language": "ru"}]  # the allowlist still applies
    assert not os.path.lexists(cmd_path)


def test_axdump_is_gone(cmd_path, caplog):
    bridge, controller = _bridge()
    with caplog.at_level(logging.WARNING, logger="murmur.notch_bridge"):
        _send(cmd_path, bridge, sign_command(SECRET, 1, "axdump"))
    assert controller.calls == []
    assert "unknown notch command" in caplog.text


def test_symlink_is_removed_not_followed(cmd_path, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text(sign_command(SECRET, 1, "start"))
    os.symlink(target, cmd_path)
    bridge, controller = _bridge()
    bridge._pump_commands()
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)
    assert target.exists()  # only the link went


def test_fifo_does_not_hang_the_bridge(cmd_path):
    os.mkfifo(cmd_path)
    bridge, controller = _bridge()
    started = time.monotonic()
    bridge._pump_commands()
    assert time.monotonic() - started < 1.0
    assert controller.calls == []
    assert not os.path.lexists(cmd_path)


def test_reads_one_lines_worth_at_most(cmd_path):
    bridge, controller = _bridge()
    _send(cmd_path, bridge, sign_command(SECRET, 1, "start") + " " * notch_bridge.MAX_CMD_BYTES)
    assert controller.calls == ["start"]  # trailing junk past the cap is never read
    _send(cmd_path, bridge, "x" * notch_bridge.MAX_CMD_BYTES + sign_command(SECRET, 2, "stop"))
    assert controller.calls == ["start"]
    assert not os.path.lexists(cmd_path)


# -- the secret hand-over ------------------------------------------------------

def test_parse_secret():
    assert parse_secret(SECRET.hex() + "\n") == SECRET
    assert parse_secret(SECRET.hex().upper()) == SECRET
    for bad in (None, "", "\n", SECRET.hex()[:-2], SECRET.hex() + "ab",
                "zz" + SECRET.hex()[2:], "start"):
        assert parse_secret(bad) is None


def test_read_secret_from_a_pipe():
    r, w = os.pipe()
    os.write(w, (SECRET.hex() + "\n").encode())
    os.close(w)
    with os.fdopen(r) as stream:
        assert read_secret(stream, timeout=1.0) == SECRET


def test_read_secret_from_an_empty_or_silent_pipe():
    r, w = os.pipe()
    os.close(w)  # NotchNest gone, or /dev/null-like: EOF right away
    with os.fdopen(r) as stream:
        assert read_secret(stream, timeout=1.0) is None

    r, w = os.pipe()  # a launcher that never writes mustn't hang startup
    try:
        with os.fdopen(r) as stream:
            started = time.monotonic()
            assert read_secret(stream, timeout=0.1) is None
            assert time.monotonic() - started < 1.0
    finally:
        os.close(w)


def test_read_secret_never_reads_a_terminal():
    class Terminal:
        def isatty(self):
            return True

        def readline(self):
            raise AssertionError("must not block on a terminal")

    assert read_secret(Terminal()) is None
    assert read_secret(None) is None
