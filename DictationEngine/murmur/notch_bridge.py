"""Optional bridge that mirrors Murmur's live state to NotchNest and accepts a
simple command to toggle dictation.

Entirely additive and crash-guarded: nothing here is allowed to affect
dictation. It talks to NotchNest through two files in ~/.murmur — no sockets,
no config, so both sides stay trivial:

    notch.json  (Murmur  -> NotchNest)  {"state","text","ts","running","settings"}
                                        ("text" is cleared TEXT_TTL s after it appears)
    notch.cmd   (NotchNest -> Murmur)   <counter> <mac> <command>, where
                                        <command> is toggle|start|stop|cancel
                                        or: set <dotted.key> <json-value>

The "settings" block mirrors the dictation settings NotchNest exposes in its
own Settings window; "set" commands write through Murmur's comment-preserving
config writer, and the config hot-reload applies them within a second.

Commands are signed, because anything running as this user can write to
~/.murmur, and a `start` it slipped in would record under NotchNest's
microphone grant. When NotchNest spawns the engine it makes a random 32-byte
secret and writes it, hex, as one line into the engine's stdin: a pipe only
NotchNest holds, so the secret is never on disk, on a command line or in the
environment (main.py reads it with read_secret). Every command then carries

    <mac>      hex HMAC-SHA256(secret, "<counter> <command>")
    <counter>  1, 2, 3, ... per engine launch; it must always grow

notch.cmd is readable by the very processes this guards against, so the
secret itself never goes in it: the MAC means a line seen once can't be edited
into another command, and the counter means it can't be replayed. Unsigned,
mis-signed and replayed commands are logged (without their text) and dropped,
and the file is deleted either way. With no secret (a plain `python main.py`
dev run, or run.sh) the bridge still publishes notch.json but ignores
notch.cmd entirely.

The bridge only ever *reads* controller state and calls the same public
controller methods the global hotkeys already call from a background thread,
so it introduces no new threading assumptions.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import select
import stat
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
    "audio.always_on_capture",
]

# How long a finished transcript stays in notch.json. NotchNest reads the file
# several times a second and keeps its own in-memory history, so the words
# needn't sit on disk after that.
TEXT_TTL = 30.0

# A genuine command is one short line; there's no reason to read more.
MAX_CMD_BYTES = 4096
_SECRET = re.compile(r"[0-9a-fA-F]{64}")  # 32 bytes, hex
_COUNTER = re.compile(r"[1-9][0-9]{0,17}")
_MAC = re.compile(r"[0-9a-f]{64}")


def parse_secret(line) -> bytes | None:
    """The command key in NotchNest's stdin line, or None if it isn't one."""
    line = (line or "").strip()
    return bytes.fromhex(line) if _SECRET.fullmatch(line) else None


def read_secret(stream, timeout: float = 5.0) -> bytes | None:
    """Reads the secret line NotchNest writes into the engine's stdin.

    None, which leaves notch.cmd disabled, for a terminal, /dev/null, a closed
    stdin, or a pipe that stays silent for `timeout` seconds (so a launcher
    that hands over a pipe and never writes can't hang startup)."""
    try:
        if stream is None or stream.isatty():
            return None
        ready, _, _ = select.select([stream], [], [], timeout)
        return parse_secret(stream.readline()) if ready else None
    except (OSError, ValueError):
        return None


def sign_command(secret: bytes, counter: int, command: str) -> str:
    """The notch.cmd line for `command`, exactly as NotchNest writes it."""
    mac = hmac.new(secret, f"{counter} {command}".encode(), hashlib.sha256).hexdigest()
    return f"{counter} {mac} {command}"


class NotchBridge:
    def __init__(self, controller, poll_interval: float = 0.25, heartbeat: float = 1.0,
                 secret: bytes | None = None):
        self.controller = controller
        self.poll_interval = poll_interval
        self.heartbeat = heartbeat
        self._secret = secret
        self._last_counter = 0
        self._thread = None
        self._stop = threading.Event()
        self._last_snapshot = None
        self._last_write_ts = 0.0
        self._text = ""
        self._text_since = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="notch-bridge", daemon=True)
        self._thread.start()
        log.info("notch bridge started (state=%s, cmd=%s)", STATE_PATH, CMD_PATH)
        if self._secret is None:
            log.info("notch.cmd commands disabled: no command secret from NotchNest")

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
        raw = self._take_command_file()
        if not raw:
            return
        command = self._authenticate(raw)
        if command is None:
            return
        cmd = command.lower()

        if cmd.startswith("set "):
            self._apply_setting(command)
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

    def _take_command_file(self) -> str | None:
        """Reads notch.cmd and deletes it, whatever it held. Doesn't follow a
        symlink or block on a FIFO left in its place, and reads one line's
        worth at most."""
        if not os.path.lexists(CMD_PATH):
            return None
        raw = None
        try:
            fd = os.open(CMD_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    raw = os.read(fd, MAX_CMD_BYTES).decode("utf-8", "replace").strip()
            finally:
                os.close(fd)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("notch command ignored: notch.cmd isn't a readable file")
        finally:
            try:
                os.remove(CMD_PATH)
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("could not delete notch.cmd")
        return raw

    def _authenticate(self, raw: str) -> str | None:
        """The command in a correctly signed, fresh notch.cmd line, else None.
        Rejections are logged without the line: it didn't come from NotchNest,
        and whatever it says doesn't belong in the log."""
        if self._secret is None:
            log.warning("notch command ignored: commands are disabled (no secret)")
            return None
        parts = raw.split(" ", 2)
        if len(parts) != 3 or not _COUNTER.fullmatch(parts[0]) or not _MAC.fullmatch(parts[1]):
            log.warning("notch command ignored: not signed")
            return None
        counter, mac, command = parts
        expected = hmac.new(self._secret, f"{counter} {command}".encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac.encode(), expected.encode()):
            log.warning("notch command ignored: wrong signature")
            return None
        if int(counter) <= self._last_counter:
            log.warning("notch command ignored: replayed (counter %s)", counter)
            return None
        self._last_counter = int(counter)
        return command

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
        now = time.time()
        text = self._shared_text(getattr(self.controller, "last_final", "") or "", now)
        error = getattr(self.controller, "last_error", None) or ""
        settings = self._settings_snapshot()
        snapshot = (state, text, error, json.dumps(settings, sort_keys=True))
        # Write on change, plus a periodic heartbeat so NotchNest can tell the
        # difference between "idle" and "Murmur isn't running".
        if snapshot != self._last_snapshot or (now - self._last_write_ts) >= self.heartbeat:
            self._atomic_write({
                "state": state, "text": text, "ts": now, "running": True,
                "error": error, "settings": settings,
            })
            self._last_snapshot = snapshot
            self._last_write_ts = now

    def _shared_text(self, text: str, now: float) -> str:
        """The latest transcript for TEXT_TTL seconds after it appears, then ""."""
        if text != self._text:
            self._text, self._text_since = text, now
        return text if now - self._text_since < TEXT_TTL else ""

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
