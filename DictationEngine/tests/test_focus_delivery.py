"""Focus-race guardrails: dictated text must land in the app dictation
started in — never in whatever app happens to be frontmost after the ~1-3s
of transcription/cleanup (seen live: a paste delivered into Murmur itself)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import murmur.inject as inject_mod
from murmur.config import Config
from murmur.inject import TextInjector


def _cfg(tmp_path, extra=""):
    p = os.path.join(tmp_path, "c.yaml")
    with open(p, "w") as f:
        f.write("insertion:\n  mode: paste\n" + extra)
    return Config(p)


def _injector(tmp_path, extra="", paste_ok=True):
    inj = TextInjector(_cfg(tmp_path, extra))
    inj._pastes = []
    inj._insert_by_paste = lambda text: inj._pastes.append(text) or paste_ok
    return inj


def test_same_app_pastes(tmp_path, monkeypatch):
    inj = _injector(tmp_path)
    monkeypatch.setattr(inject_mod, "frontmost_bundle_id", lambda: "com.apple.TextEdit")
    monkeypatch.setattr(inject_mod, "own_bundle_id", lambda: "com.arsen.murmur")
    status = inj.insert("hello", target_bundle="com.apple.TextEdit", target_pid=123)
    assert status == "inserted"
    assert inj._pastes == ["hello"]


def test_wrong_app_reactivation_success(tmp_path, monkeypatch):
    inj = _injector(tmp_path)
    monkeypatch.setattr(inject_mod, "own_bundle_id", lambda: "com.arsen.murmur")
    # frontmost flips to the target after re-activation is attempted
    state = {"front": "com.google.Chrome", "activated": False}

    def fake_front():
        return "com.apple.TextEdit" if state["activated"] else state["front"]

    monkeypatch.setattr(inject_mod, "frontmost_bundle_id", fake_front)

    def fake_ensure(bundle, pid, timeout=1.0):
        # simulate the real method's contract using the fake workspace
        if fake_front() == bundle:
            return True
        state["activated"] = True  # NSRunningApplication.activate succeeded
        return fake_front() == bundle

    inj._ensure_frontmost = fake_ensure
    status = inj.insert("hello", target_bundle="com.apple.TextEdit", target_pid=1)
    assert status == "inserted"
    assert inj._pastes == ["hello"]


def test_wrong_app_reactivation_failure_refuses_paste(tmp_path, monkeypatch):
    inj = _injector(tmp_path)
    monkeypatch.setattr(inject_mod, "frontmost_bundle_id", lambda: "com.google.Chrome")
    monkeypatch.setattr(inject_mod, "own_bundle_id", lambda: "com.arsen.murmur")
    inj._ensure_frontmost = lambda bundle, pid, timeout=1.0: False
    status = inj.insert("hello", target_bundle="com.apple.TextEdit", target_pid=1)
    assert status == "wrong_app"
    assert inj._pastes == []  # nothing pasted into the wrong app


def test_target_is_murmur_itself_refuses(tmp_path, monkeypatch):
    inj = _injector(tmp_path)
    monkeypatch.setattr(inject_mod, "own_bundle_id", lambda: "com.arsen.murmur")
    status = inj.insert("hello", target_bundle="com.arsen.murmur", target_pid=1)
    assert status == "wrong_app"
    assert inj._pastes == []


def test_follow_focus_true_keeps_old_behavior(tmp_path, monkeypatch):
    inj = _injector(tmp_path, extra="  follow_focus: true\n")
    monkeypatch.setattr(inject_mod, "frontmost_bundle_id", lambda: "com.google.Chrome")
    called = {"ensure": False}
    inj._ensure_frontmost = lambda *a, **k: called.__setitem__("ensure", True) or False
    status = inj.insert("hello", target_bundle="com.apple.TextEdit", target_pid=1)
    assert status == "inserted"  # pasted into Chrome, as the old behavior did
    assert inj._pastes == ["hello"]
    assert called["ensure"] is False  # no re-activation attempted


def test_no_target_means_untargeted_insert(tmp_path, monkeypatch):
    inj = _injector(tmp_path)
    status = inj.insert("hello")
    assert status == "inserted"
    assert inj._pastes == ["hello"]


def test_controller_deliver_falls_back_to_clipboard(tmp_path):
    """wrong_app from the injector => clipboard + notification, no crash."""
    from murmur.controller import Controller

    cfg = _cfg(tmp_path, "llm:\n  backend: none\n")
    ctrl = Controller(cfg, os.path.join(tmp_path, "m.db"))
    events = {"clip": None, "notified": []}
    ctrl.injector.insert = lambda text, target_bundle=None, target_pid=None: "wrong_app"
    ctrl.injector.copy_to_clipboard = lambda text: events.__setitem__("clip", text) or True
    ctrl.notify = lambda title, msg: events["notified"].append(msg)
    ctrl._deliver("hello world", "com.apple.TextEdit", 42)
    assert events["clip"] == "hello world"
    assert any("clipboard" in m for m in events["notified"])
