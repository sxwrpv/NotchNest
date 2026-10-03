"""Security-audit fixes: local-only LLM URLs, no transcripts in logs or left
in notch.json, private ~/.murmur, pinned model revisions."""

import logging
import os
import stat

import pytest

from murmur import llm, models, notch_bridge, paths
from murmur.privacy import said


# ---- NN-6: the Ollama URL must point at this Mac ------------------------------

@pytest.mark.parametrize("url, expected", [
    ("http://localhost:11434", "http://localhost:11434"),
    ("http://localhost:11434/", "http://localhost:11434"),
    ("  http://127.0.0.1:11434 ", "http://127.0.0.1:11434"),
    ("http://[::1]:11434", "http://[::1]:11434"),
    ("https://LOCALHOST", "https://localhost"),
])
def test_local_urls_are_accepted(url, expected):
    assert llm.local_base_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://localhost.example.com",        # passed the old prefix check
    "http://127.0.0.1.nip.io:11434",       # so did this
    "http://localhost@evil.example:11434",
    "http://user:pw@localhost:11434",
    "http://evil.example#@localhost",
    "http://localhost:11434/api/chat",
    "http://localhost:11434/?next=http://evil.example",
    "http://localhost:99999",
    "ftp://localhost",
    "localhost:11434",
    "",
])
def test_remote_or_odd_urls_are_refused(url):
    with pytest.raises(ValueError):
        llm.local_base_url(url)


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_ollama_backend_never_contacts_a_remote_url(monkeypatch):
    calls = []

    class Session:
        trust_env = True

        def get(self, *a, **k):
            calls.append(a)

        post = get

    import requests
    monkeypatch.setattr(requests, "Session", Session)
    backend = llm.OllamaBackend(_Config({"llm.ollama.url": "http://localhost.example.com"}))
    assert backend.available() is False
    with pytest.raises(llm.LLMUnavailable):
        backend.generate("system", "my dictated words")
    assert calls == []


def test_ollama_requests_ignore_proxies_and_redirects(monkeypatch):
    seen = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "cleaned"}}

    class Session:
        trust_env = True

        def post(self, url, **kwargs):
            seen.update(url=url, trust_env=self.trust_env, **kwargs)
            return Response()

    import requests
    monkeypatch.setattr(requests, "Session", Session)
    backend = llm.OllamaBackend(_Config({"llm.ollama.url": "http://127.0.0.1:11434/"}))
    assert backend.generate("system", "words") == "cleaned"
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["allow_redirects"] is False
    assert seen["trust_env"] is False


# ---- NN-3: dictated words stay out of the log ---------------------------------

def test_log_lines_show_only_the_length_unless_debugging():
    logger = logging.getLogger("murmur.test_privacy")
    logger.setLevel(logging.INFO)
    assert said(logger, "my bank PIN is 4512") == "<19 chars>"
    assert said(logger, None) == "<0 chars>"
    logger.setLevel(logging.DEBUG)
    assert said(logger, "hello") == "'hello'"


def test_config_dir_and_its_files_become_private(tmp_path, monkeypatch):
    home = tmp_path / ".murmur"
    home.mkdir(mode=0o755)
    os.chmod(home, 0o755)
    for name in ("murmur.log", "murmur.log.2", "murmur.db", "config.yaml"):
        (home / name).write_text("x")
        os.chmod(home / name, 0o644)
    monkeypatch.setattr(paths, "CONFIG_DIR", str(home))

    paths.ensure_config_dir()

    mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)  # noqa: E731
    assert mode(home) == 0o700
    for name in ("murmur.log", "murmur.log.2", "murmur.db", "config.yaml"):
        assert mode(home / name) == 0o600


def test_a_missing_config_dir_is_created_private(tmp_path, monkeypatch):
    home = tmp_path / "fresh" / ".murmur"
    monkeypatch.setattr(paths, "CONFIG_DIR", str(home))
    paths.ensure_config_dir()
    assert stat.S_IMODE(os.stat(home).st_mode) == 0o700


def test_notch_json_drops_the_transcript_after_a_while():
    bridge = notch_bridge.NotchBridge(controller=object())
    ttl = notch_bridge.TEXT_TTL
    assert bridge._shared_text("first words", now=100.0) == "first words"
    assert bridge._shared_text("first words", now=100.0 + ttl - 1) == "first words"
    assert bridge._shared_text("first words", now=100.0 + ttl + 1) == ""
    # a new transcript is shared again, for its own window
    assert bridge._shared_text("second", now=500.0) == "second"
    assert bridge._shared_text("", now=501.0) == ""


# ---- NN-7: models load at the revision they were tested with ------------------

def test_installer_models_are_pinned_to_commits():
    import setup_engine
    from murmur.transcriber import resolve_model

    for repo in (setup_engine.LLM_LARGE, setup_engine.LLM_SMALL,
                 resolve_model(setup_engine.ASR_DEFAULT)):
        revision = models.pinned_revision(repo)
        assert revision and len(revision) == 40 and int(revision, 16) >= 0


def test_local_model_path_prefers_the_cached_pinned_snapshot(monkeypatch):
    import huggingface_hub

    calls = []

    def fake_snapshot_download(repo, revision=None, local_files_only=False):
        calls.append((repo, revision, local_files_only))
        if not local_files_only:
            return "/downloaded"
        if revision == "cached-rev":
            return "/cache/snapshot"
        raise FileNotFoundError

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setitem(models.PINNED_REVISIONS, "org/cached", "cached-rev")
    monkeypatch.setitem(models.PINNED_REVISIONS, "org/missing", "missing-rev")

    assert models.local_model_path("org/cached") == "/cache/snapshot"
    assert calls == [("org/cached", "cached-rev", True)]  # no network on a warm start

    calls.clear()
    assert models.local_model_path("org/missing") == "/downloaded"
    assert calls == [("org/missing", "missing-rev", True), ("org/missing", "missing-rev", False)]

    calls.clear()
    assert models.local_model_path("org/unpinned") == "/downloaded"
    assert calls[-1] == ("org/unpinned", None, False)  # newest, as before


def test_local_folders_are_used_as_they_are(tmp_path):
    assert models.local_model_path(str(tmp_path)) == str(tmp_path)
