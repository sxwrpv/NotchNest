#!/usr/bin/env python3
"""First-run provisioning for the dictation engine, driven by NotchNest's setup
assistant. Prints one JSON object per line on stdout so the app can show
progress; anything else (library chatter) goes to stderr and is only logged.

    python setup_engine.py configure   # fresh ~/.murmur/config.yaml tuned to this Mac
    python setup_engine.py models      # download the configured Whisper + LLM weights
    python setup_engine.py verify      # import-check the runtime, confirm models are cached

Every command exits 0 on success and 1 after emitting {"event": "error", ...}.
"""

import os

# Same privacy/transfer settings as main.py, set before huggingface_hub loads.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import json
import sys
import threading
import time

# Weights per RAM tier. Every Apple Silicon Mac has at least 8 GB; the 3B
# cleanup model wants the headroom of 16 GB, the 1.5B one fits comfortably in 8.
LLM_LARGE = "mlx-community/Qwen2.5-3B-Instruct-4bit"
LLM_SMALL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
ASR_DEFAULT = "large-v3-turbo-q4"


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def fail(message: str) -> None:
    emit("error", message=message)
    sys.exit(1)


def ram_gb() -> float:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def hardware_profile(ram: float) -> dict:
    return {
        "asr.model": ASR_DEFAULT,
        "llm.mlx.model": LLM_LARGE if ram >= 12 else LLM_SMALL,
        "llm.cleanup_enabled": True,
    }


# ---- configure ----------------------------------------------------------------

def cmd_configure() -> None:
    from murmur.config import write_config_values
    from murmur.paths import CONFIG_PATH, bootstrap_config

    fresh = bootstrap_config()
    ram = ram_gb()
    if not fresh:
        # Never second-guess a config someone already has (or has edited).
        emit("configured", fresh=False, ram_gb=round(ram, 1), path=CONFIG_PATH)
        return
    profile = hardware_profile(ram)
    if not write_config_values(CONFIG_PATH, profile):
        fail(f"Could not write {CONFIG_PATH}")
    emit("configured", fresh=True, ram_gb=round(ram, 1), path=CONFIG_PATH, values=profile)


# ---- models -------------------------------------------------------------------

def wanted_models() -> list:
    """(label, repo) for every model the current config will load."""
    from murmur.config import Config
    from murmur.paths import CONFIG_PATH, bootstrap_config
    from murmur.transcriber import resolve_model

    bootstrap_config()
    cfg = Config(CONFIG_PATH)
    models = [("Speech model", resolve_model(str(cfg.get("asr.model", ASR_DEFAULT))))]
    backend = str(cfg.get("llm.backend", "auto")).lower()
    if cfg.get("llm.cleanup_enabled", True) and backend in ("auto", "mlx"):
        models.append(("AI cleanup model", str(cfg.get("llm.mlx.model", LLM_LARGE))))
    return models


def _repo_dir(repo: str) -> str:
    from huggingface_hub import constants

    return os.path.join(constants.HF_HUB_CACHE, "models--" + repo.replace("/", "--"))


def _present_bytes(repo: str, sha: str, files: dict) -> int:
    """Bytes of `files` already downloaded: finished files resolve through the
    snapshot symlinks; in-flight ones are `.incomplete` blobs."""
    snap = os.path.join(_repo_dir(repo), "snapshots", sha)
    done = 0
    for name, size in files.items():
        if os.path.exists(os.path.join(snap, name)):
            done += size
    blobs = os.path.join(_repo_dir(repo), "blobs")
    try:
        for entry in os.scandir(blobs):
            if entry.name.endswith(".incomplete"):
                done += entry.stat().st_size
    except FileNotFoundError:
        pass
    return done


def _download(label: str, repo: str, index: int, count: int) -> None:
    from huggingface_hub import HfApi, snapshot_download
    from murmur.models import pinned_revision

    base = {"item": label, "repo": repo, "index": index, "count": count}
    revision = pinned_revision(repo)  # the engine loads exactly this commit
    try:
        info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    except Exception as e:
        # Offline: fine if a previous run already fetched it.
        try:
            snapshot_download(repo, revision=revision, local_files_only=True)
            emit("model_ready", cached=True, offline=True, **base)
            return
        except Exception:
            fail(f"Can't reach Hugging Face to download {repo} ({type(e).__name__}). "
                 "Check the internet connection and retry.")

    files = {s.rfilename: int(s.size or 0) for s in info.siblings}
    total = sum(files.values())
    if _present_bytes(repo, info.sha, files) >= total:
        emit("progress", done=total, total=total, **base)
        emit("model_ready", cached=True, **base)
        return

    for attempt in range(1, 4):
        result = {}

        def run():
            try:
                snapshot_download(repo, revision=revision)
            except Exception as e:  # surfaced after the join below
                result["error"] = e

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            emit("progress", done=min(_present_bytes(repo, info.sha, files), total),
                 total=total, **base)
            worker.join(0.5)
        if "error" not in result:
            emit("progress", done=total, total=total, **base)
            emit("model_ready", cached=False, **base)
            return
        print(f"download attempt {attempt} for {repo} failed: {result['error']!r}",
              file=sys.stderr, flush=True)
        time.sleep(2 * attempt)  # partial blobs resume on the next attempt
    fail(f"Downloading {repo} failed: {result['error']}")


def cmd_models() -> None:
    models = wanted_models()
    for i, (label, repo) in enumerate(models, start=1):
        _download(label, repo, i, len(models))
    emit("done", models=[repo for _, repo in models])


# ---- verify -------------------------------------------------------------------

def cmd_verify() -> None:
    try:
        import AppKit  # noqa: F401
        import ApplicationServices  # noqa: F401
        import mlx.core as mx
        import mlx_lm  # noqa: F401
        import mlx_whisper  # noqa: F401
        import Quartz  # noqa: F401
        import sounddevice  # noqa: F401
        import yaml  # noqa: F401
    except Exception as e:
        fail(f"Engine runtime is incomplete: {e}")

    if not mx.metal.is_available():
        fail("Metal isn't available — dictation needs an Apple Silicon Mac.")

    from huggingface_hub import snapshot_download
    from murmur.models import pinned_revision

    missing = []
    for _, repo in wanted_models():
        try:
            snapshot_download(repo, revision=pinned_revision(repo), local_files_only=True)
        except Exception:
            missing.append(repo)
    if missing:
        fail("Models not downloaded yet: " + ", ".join(missing))
    emit("verified", python=sys.version.split()[0], mlx=mx.__version__)


COMMANDS = {"configure": cmd_configure, "models": cmd_models, "verify": cmd_verify}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        COMMANDS[sys.argv[1]]()
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
