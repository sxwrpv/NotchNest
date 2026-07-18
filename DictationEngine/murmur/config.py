"""YAML config with defaults, dotted-path access, and hot-reload on save."""

import copy
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

import yaml

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "audio": {
        "sample_rate": 16000,
        "preroll_ms": 500,
        # Keep a tiny always-on capture stream so the 500ms before the hotkey
        # isn't lost. Set to false if you'd rather the mic only open while
        # recording (loses pre-roll).
        "always_on_capture": True,
        "input_device": None,  # null = system default
        "min_utterance_s": 0.35,
    },
    "hotkeys": {
        # Double-tap toggles recording; press-and-hold is push-to-talk.
        "dictation_key": "fn",
        # Hold while text is selected to speak a rewrite instruction.
        "command_key": "right_option",
        "double_tap_window_ms": 400,
        "hold_threshold_ms": 350,
    },
    "asr": {
        # Short names map to mlx-community repos; a full HF repo id also works.
        # Options: tiny.en, base.en, small.en, small, medium, large-v3-turbo,
        # large-v3-turbo-q4 (default: multilingual, ~3x smaller than fp16
        # with near-identical accuracy and faster cold start)
        "model": "large-v3-turbo-q4",
        # auto = per-utterance language detection (mix EN/RU freely),
        # or pin an ISO code: en, ru, de, ...
        "language": "auto",
        "partials": True,
        "partial_interval_ms": 1500,
        "partial_window_s": 12,
    },
    "llm": {
        # auto: use Ollama if it's running, else in-process MLX, else rule-based.
        # Values: auto | ollama | mlx | none
        "backend": "auto",
        "cleanup_enabled": True,
        "max_tokens": 1024,
        "temperature": 0.15,
        "ollama": {
            "url": "http://localhost:11434",
            "model": "qwen2.5:3b-instruct",
            # Unload from RAM after 5 idle minutes — important on 16GB machines.
            "keep_alive": "5m",
        },
        "mlx": {
            "model": "mlx-community/Qwen2.5-3B-Instruct-4bit",
            "idle_unload_s": 300,
        },
    },
    "insertion": {
        "mode": "paste",  # paste | type
        "restore_clipboard": True,
        # When true, the transcript stays on the clipboard after pasting
        # (skips the restore of your previous clipboard contents).
        "keep_on_clipboard": False,
        # false (default): text is delivered to the app dictation STARTED in,
        # re-activating it if you switched windows during transcription; if
        # that fails the text goes to the clipboard instead of a wrong app.
        # true: paste into whatever app is frontmost when processing finishes.
        "follow_focus": False,
        "paste_delay_ms": 80,
    },
    "dictionary": {
        "entries": [],
        "replacements": {},
        "auto_learn": True,
    },
    "snippets": {},
    "styles": {
        "default": "neutral",
        "presets": {},  # extra/overriding presets: name -> instruction text
        "per_app": {},  # bundle id -> preset name
    },
    "overlay": {
        "enabled": True,
        "width": 400,
        # Keep the status pill on screen at all times (idle/listening/
        # processing), not just during an active dictation session. Set to
        # false to revert to the old on-demand-during-dictation behavior.
        "always_visible": True,
        # Anchor of the pill: x = horizontal center, y = bottom edge, in
        # global screen coordinates (origin bottom-left). null = default
        # bottom-center. Written automatically when you drag the pill.
        "position": None,
    },
    "ui": {
        "show_notifications": True,
    },
    "logging": {
        "level": "INFO",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Thread-safe config wrapper. `get("a.b.c", default)` reads live values,
    so most components pick up hot-reloaded changes automatically."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = copy.deepcopy(DEFAULTS)
        self._mtime: float = 0.0
        self._callbacks: list[Callable[["Config"], None]] = []
        self._watcher: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.load()

    # -- access ---------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data(self) -> dict:
        with self._lock:
            return self._data

    # -- loading / watching ----------------------------------------------
    def load(self) -> bool:
        """(Re)load from disk. Returns True if load succeeded."""
        try:
            raw = {}
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                if not isinstance(raw, dict):
                    raise ValueError("top-level YAML must be a mapping")
            merged = _deep_merge(DEFAULTS, raw)
            with self._lock:
                self._data = merged
                try:
                    self._mtime = os.path.getmtime(self.path)
                except OSError:
                    self._mtime = 0.0
            return True
        except Exception as e:  # keep previous config on a bad edit
            log.error("config reload failed, keeping previous config: %s", e)
            return False

    def on_reload(self, cb: Callable[["Config"], None]) -> None:
        self._callbacks.append(cb)

    def start_watching(self, interval_s: float = 1.0) -> None:
        if self._watcher:
            return

        def _watch():
            while not self._stop.wait(interval_s):
                try:
                    mtime = os.path.getmtime(self.path)
                except OSError:
                    continue
                if mtime != self._mtime:
                    ok = self.load()
                    if ok:
                        log.info("config hot-reloaded from %s", self.path)
                        for cb in self._callbacks:
                            try:
                                cb(self)
                            except Exception:
                                log.exception("config reload callback failed")

        self._watcher = threading.Thread(target=_watch, name="config-watch", daemon=True)
        self._watcher.start()

    def stop_watching(self) -> None:
        self._stop.set()


import re as _re  # noqa: E402  (kept local to the writer below)


def _render_scalar(value) -> str:
    """YAML-safe single-token rendering of a scalar (handles quoting)."""
    if value is None:
        return "null"
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[:-4].strip()
    if dumped == "...":
        dumped = "null"
    return dumped


def _is_flat_scalar_dict(value) -> bool:
    return (
        isinstance(value, dict)
        and 0 < len(value) <= 4
        and all(not isinstance(v, (dict, list)) for v in value.values())
    )


def _render_entry(key: str, value, indent: int) -> list:
    """Render `key: value` as YAML lines at the given indent."""
    pad = " " * indent
    if value is None:
        return [f"{pad}{key}: null"]
    if isinstance(value, dict) and not value:
        return [f"{pad}{key}: {{}}"]
    if isinstance(value, list) and not value:
        return [f"{pad}{key}: []"]
    if _is_flat_scalar_dict(value):
        inner = ", ".join(f"{k}: {_render_scalar(v)}" for k, v in value.items())
        return [f"{pad}{key}: {{{inner}}}"]
    if isinstance(value, (dict, list)):
        dumped = yaml.safe_dump(
            value, default_flow_style=False, allow_unicode=True, sort_keys=False
        ).rstrip("\n")
        child_pad = " " * (indent + 2)
        return [f"{pad}{key}:"] + [child_pad + ln for ln in dumped.split("\n")]
    return [f"{pad}{key}: {_render_scalar(value)}"]


def _block_end(lines: list, start: int, indent: int) -> int:
    """Index one past the last line belonging to the block whose key line is
    at `start` with the given indent (children, blanks, comments)."""
    j = start + 1
    end = j
    while j < len(lines):
        ln = lines[j]
        if not ln.strip():
            j += 1
            continue
        ln_indent = len(ln) - len(ln.lstrip())
        if ln_indent <= indent and not ln.lstrip().startswith("#"):
            break
        if ln_indent <= indent and ln.lstrip().startswith("#"):
            break  # comment at parent level belongs to the next key
        j += 1
        end = j
    return end


def _set_in_lines(lines: list, key_path: list, value, indent: int, lo: int, hi: int) -> list:
    """Recursive surgical set of key_path (within lines[lo:hi] at `indent`)."""
    key = key_path[0]
    key_re = _re.compile(r"^(\s*)" + _re.escape(key) + r"\s*:(.*)$")
    i = lo
    while i < hi:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ln_indent = len(line) - len(line.lstrip())
        if ln_indent != indent:
            i += 1
            continue
        m = key_re.match(line)
        if not m:
            i += 1
            continue
        rest = m.group(2).strip()
        rest_no_comment = rest.split("#", 1)[0].strip() if not rest.startswith("#") else ""
        if len(key_path) == 1:
            # replace this entry (line + any nested block under it)
            end = _block_end(lines, i, indent)
            return lines[:i] + _render_entry(key, value, indent) + lines[end:]
        if rest_no_comment.startswith("{"):
            # inline flow mapping: merge in python, re-render as one flow line
            data = yaml.safe_load(rest_no_comment) or {}
            node = data
            for part in key_path[1:-1]:
                node = node.setdefault(part, {})
            node[key_path[-1]] = value
            flow = yaml.safe_dump(data, default_flow_style=True, allow_unicode=True).strip()
            return lines[:i] + [" " * indent + f"{key}: {flow}"] + lines[i + 1:]
        # block form: recurse into children
        end = _block_end(lines, i, indent)
        # detect child indent step from first child key line
        child_indent = None
        for j in range(i + 1, end):
            ln = lines[j]
            if ln.strip() and not ln.lstrip().startswith("#"):
                child_indent = len(ln) - len(ln.lstrip())
                break
        if child_indent is None:
            child_indent = indent + 2
        result = _set_in_lines(lines, key_path[1:], value, child_indent, i + 1, end)
        if result is not None:
            return result
        # child key missing: insert rendered subtree at end of this block
        subtree = value
        for part in reversed(key_path[2:]):
            subtree = {part: subtree}
        rendered = _render_entry(key_path[1], subtree, child_indent)
        return lines[:end] + rendered + lines[end:]
    return None  # key not found at this level


def write_config_values(path: str, updates: dict) -> bool:
    """Surgically set dotted-path keys in the YAML file, preserving comments
    and unrelated formatting (a full PyYAML round-trip would strip comments).
    Replacing a dict/list subtree loses comments only inside that subtree.
    Creates missing sections. Returns True on success."""
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = []
        for dotted, value in updates.items():
            key_path = dotted.split(".")
            result = _set_in_lines(lines, key_path, value, 0, 0, len(lines))
            if result is None:
                # no top-level key: append a fresh subtree at the end
                subtree = value
                for part in reversed(key_path[1:]):
                    subtree = {part: subtree}
                appended = _render_entry(key_path[0], subtree, 0)
                if lines and lines[-1].strip():
                    lines = lines + [""]
                lines = lines + appended
            else:
                lines = result
        # sanity: the result must still parse
        parsed = yaml.safe_load("\n".join(lines) or "{}")
        if parsed is not None and not isinstance(parsed, dict):
            raise ValueError("config would no longer be a mapping")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception("failed to write config values %s", list(updates))
        return False


def write_overlay_position(path: str, x: float, y: float) -> bool:
    """Persist `overlay.position: {x, y}` (kept as a thin wrapper around the
    generic comment-preserving writer)."""
    return write_config_values(
        path, {"overlay.position": {"x": round(float(x), 1), "y": round(float(y), 1)}}
    )
