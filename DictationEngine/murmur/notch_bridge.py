"""Optional bridge that mirrors Murmur's live state to NotchNest and accepts a
simple command to toggle dictation.

Entirely additive and crash-guarded: nothing here is allowed to affect
dictation. It talks to NotchNest through two files in ~/.murmur — no sockets,
no config, so both sides stay trivial:

    notch.json  (Murmur  -> NotchNest)  {"state","text","ts","running","settings"}
    notch.cmd   (NotchNest -> Murmur)   one word: toggle|start|stop|cancel
                                        or: set <dotted.key> <json-value>

The "settings" block mirrors the dictation settings NotchNest exposes in its
own Settings window; "set" commands write through Murmur's comment-preserving
config writer, and the config hot-reload applies them within a second.

The bridge only ever *reads* controller state and calls the same public
controller methods the global hotkeys already call from a background thread,
so it introduces no new threading assumptions.
"""

import json
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger("murmur.notch_bridge")

CONFIG_DIR = os.path.expanduser("~/.murmur")
STATE_PATH = os.path.join(CONFIG_DIR, "notch.json")
CMD_PATH = os.path.join(CONFIG_DIR, "notch.cmd")

# The settings NotchNest may read and write. An allowlist keeps arbitrary
# config keys out of reach of the cmd file.
SETTINGS_KEYS = [
    "hotkeys.dictation_key",
    "hotkeys.command_key",
    "asr.model",
    "asr.language",
    "asr.partials",
    "llm.cleanup_enabled",
    "llm.backend",
    "insertion.mode",
    "ui.show_notifications",
]


class NotchBridge:
    def __init__(self, controller, poll_interval: float = 0.25, heartbeat: float = 1.0):
        self.controller = controller
        self.poll_interval = poll_interval
        self.heartbeat = heartbeat
        self._thread = None
        self._stop = threading.Event()
        self._last_snapshot = None
        self._last_write_ts = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="notch-bridge", daemon=True)
        self._thread.start()
        log.info("notch bridge started (state=%s, cmd=%s)", STATE_PATH, CMD_PATH)

    def stop(self):
        self._stop.set()

    # -- main loop ----------------------------------------------------------
    def _run(self):
        while not self._stop.wait(self.poll_interval):
            try:
                self._pump_commands()
                self._publish()
            except Exception:
                log.exception("notch bridge cycle failed (ignored)")

    # -- NotchNest -> Murmur -------------------------------------------------
    def _pump_commands(self):
        try:
            if not os.path.exists(CMD_PATH):
                return
            with open(CMD_PATH, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            os.remove(CMD_PATH)
        except FileNotFoundError:
            return
        except Exception:
            log.exception("failed reading notch command")
            return

        if not raw:
            return
        cmd = raw.lower()

        if cmd.startswith("set "):
            self._apply_setting(raw)
            return

        actions = {
            "toggle": self.controller.toggle_dictation,
            "start": self.controller.ptt_start,
            "stop": self.controller.ptt_stop,
            "cancel": self.controller.cancel,
        }
        fn = actions.get(cmd)
        if fn is None:
            log.warning("unknown notch command: %r", cmd)
            return
        try:
            fn()
            log.info("notch command dispatched: %s", cmd)
        except Exception:
            log.exception("notch command %s raised", cmd)

    def _apply_setting(self, raw: str):
        """`set <dotted.key> <json-value>` — writes through the comment-
        preserving config writer; the hot-reload watcher applies it."""
        parts = raw.split(None, 2)
        if len(parts) != 3:
            log.warning("malformed set command: %r", raw)
            return
        key, value_raw = parts[1], parts[2]
        if key not in SETTINGS_KEYS:
            log.warning("notch set rejected (key not allowlisted): %r", key)
            return
        try:
            value = json.loads(value_raw)
        except ValueError:
            value = value_raw  # bare string
        try:
            from .config import write_config_values
            from .paths import CONFIG_PATH

            if write_config_values(CONFIG_PATH, {key: value}):
                log.info("notch set %s = %r", key, value)
            else:
                log.warning("notch set %s failed (writer returned False)", key)
        except Exception:
            log.exception("notch set %s raised", key)

    # -- Murmur -> NotchNest -------------------------------------------------
    def _publish(self):
        try:
            state = getattr(self.controller.state, "value", str(self.controller.state))
        except Exception:
            state = "idle"
        text = getattr(self.controller, "last_final", "") or ""
        settings = self._settings_snapshot()
        now = time.time()
        snapshot = (state, text, json.dumps(settings, sort_keys=True))
        # Write on change, plus a periodic heartbeat so NotchNest can tell the
        # difference between "idle" and "Murmur isn't running".
        if snapshot != self._last_snapshot or (now - self._last_write_ts) >= self.heartbeat:
            self._atomic_write({
                "state": state, "text": text, "ts": now, "running": True,
                "settings": settings,
            })
            self._last_snapshot = snapshot
            self._last_write_ts = now

    def _settings_snapshot(self) -> dict:
        try:
            cfg = self.controller.config
            return {key: cfg.get(key) for key in SETTINGS_KEYS}
        except Exception:
            return {}

    def _atomic_write(self, payload: dict):
        try:
            fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".notch.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, STATE_PATH)  # atomic on the same filesystem
        except Exception:
            log.exception("failed writing notch state")
