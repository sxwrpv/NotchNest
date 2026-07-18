#!/usr/bin/env python3
"""Murmur entry point.

    source .venv/bin/activate
    python main.py

First run creates ~/.murmur/config.yaml from the shipped template. Logs go
to ~/.murmur/murmur.log (size-capped, rotating) and stderr.
"""

import os

# Privacy hardening, set before anything can import huggingface_hub:
# - DISABLE_TELEMETRY also stops the client's daily "agent harness registry"
#   fetch (GET /api/agent-harnesses) and extra user-agent tagging — Murmur
#   must not make surprise network calls.
# - DISABLE_XET avoids the transfer backend that stalls on this network.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import logging
import sys

from murmur.config import Config
from murmur.controller import Controller
from murmur.hotkeys import HotkeyManager
from murmur.inject import accessibility_trusted
from murmur.paths import CONFIG_PATH, DB_PATH, LOG_PATH, bootstrap_config


def setup_logging(level: str):
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    # size-capped so the always-on daemon can't grow the log forever
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console echo only for interactive runs — run.sh already redirects
    # stdout into the same log file, which would double every record.
    if sys.stdout.isatty():
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def already_running() -> bool:
    """Single-instance guard shared by every launch path (NotchNest child
    process, run.sh, plain `python main.py`). Requires an engine-specific
    path fragment so other projects' `.venv/bin/python main.py` don't match."""
    import psutil

    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            cmd = " ".join(proc.info["cmdline"] or [])
            if ("main.py" in cmd and ".venv/bin/python" in cmd
                    and ("murmur" in cmd.lower() or "dictationengine" in cmd.lower())):
                return True
        except Exception:  # NoSuchProcess, AccessDenied, zombie races
            continue
    return False


def main():
    if already_running():
        print("Murmur is already running.")
        sys.exit(0)
    first_run = bootstrap_config()
    config = Config(CONFIG_PATH)
    setup_logging(config.get("logging.level", "INFO"))
    log = logging.getLogger("murmur.main")
    log.info("Murmur starting — config at %s", CONFIG_PATH)

    if sys.platform != "darwin":
        log.error("Murmur only supports macOS.")
        sys.exit(1)

    if not accessibility_trusted():
        log.warning(
            "Accessibility permission not yet granted. Global hotkeys and "
            "text insertion will not work until you approve Murmur in "
            "System Settings -> Privacy & Security -> Accessibility."
        )

    controller = Controller(config, DB_PATH)
    hotkeys = HotkeyManager(config)
    hotkeys.on_dictation_toggle = controller.toggle_dictation
    hotkeys.on_ptt_start = controller.ptt_start
    hotkeys.on_ptt_stop = controller.ptt_stop
    hotkeys.on_command_start = controller.command_start
    hotkeys.on_command_stop = controller.command_stop

    # Optional NotchNest bridge — additive and crash-guarded; a failure here
    # must never affect dictation.
    try:
        from murmur.notch_bridge import NotchBridge

        NotchBridge(controller).start()
    except Exception:
        log.exception("notch bridge failed to start (non-fatal)")

    def _on_reload(cfg):
        controller.on_config_reload()
        # hotkeys must be rebuilt on the main thread
        from PyObjCTools import AppHelper

        AppHelper.callAfter(hotkeys.reconfigure)

    config.on_reload(_on_reload)
    config.start_watching()

    headless = "--headless" in sys.argv or os.environ.get("MURMUR_HEADLESS") == "1"
    if headless:
        # NotchNest is the UI: no menu-bar icon, no dock presence. We still
        # need the AppKit run loop on the main thread for the hotkey event
        # tap, AX text insertion, and AppHelper.callAfter dispatches.
        from AppKit import NSApplication, NSApplicationActivationPolicyProhibited
        from PyObjCTools import AppHelper

        ns_app = NSApplication.sharedApplication()
        ns_app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)

        def _boot():
            try:
                hotkeys.start()
            except Exception:
                log.exception("failed to start hotkeys")
            controller.startup()

        AppHelper.callAfter(_boot)
        log.info("running headless — NotchNest owns the UI")
        AppHelper.runEventLoop()
        return

    from murmur.menubar import MurmurMenuBar

    app = MurmurMenuBar(config, controller, hotkeys)
    if first_run:
        app.first_run = True
    app.run()


if __name__ == "__main__":
    main()
