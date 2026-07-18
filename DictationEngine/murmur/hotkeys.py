"""Global hotkeys via NSEvent monitors (no pynput — modifier-only combos like
double-tap Fn need the AppKit flagsChanged stream).

Per configured key we detect three gestures:
  - double-tap            -> toggle dictation
  - press-and-hold        -> push-to-talk (start on hold, stop on release)
  - (command key) hold    -> Command Mode capture

Requires the host process to be trusted for Accessibility; otherwise global
monitors receive nothing.
"""

import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# keyCode -> (name, modifier flag bit)
_FLAG_FUNCTION = 1 << 23
_FLAG_SHIFT = 1 << 17
_FLAG_CONTROL = 1 << 18
_FLAG_OPTION = 1 << 19
_FLAG_COMMAND = 1 << 20

KEYS = {
    "fn": (63, _FLAG_FUNCTION),
    "left_shift": (56, _FLAG_SHIFT),
    "right_shift": (60, _FLAG_SHIFT),
    "left_control": (59, _FLAG_CONTROL),
    "right_control": (62, _FLAG_CONTROL),
    "left_option": (58, _FLAG_OPTION),
    "right_option": (61, _FLAG_OPTION),
    "left_command": (55, _FLAG_COMMAND),
    "right_command": (54, _FLAG_COMMAND),
}


class _KeyGesture:
    """Tracks one physical key's tap/double-tap/hold state."""

    def __init__(
        self,
        name: str,
        double_tap_window: float,
        hold_threshold: float,
        on_double_tap: Optional[Callable[[], None]] = None,
        on_hold_start: Optional[Callable[[], None]] = None,
        on_hold_end: Optional[Callable[[], None]] = None,
    ):
        if name not in KEYS:
            raise ValueError(f"unknown hotkey '{name}' (choose from {sorted(KEYS)})")
        self.name = name
        self.keycode, self.flagmask = KEYS[name]
        self.double_tap_window = double_tap_window
        self.hold_threshold = hold_threshold
        self.on_double_tap = on_double_tap
        self.on_hold_start = on_hold_start
        self.on_hold_end = on_hold_end
        self._down = False
        self._holding = False
        self._last_tap = 0.0
        self._hold_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def handle_flags_changed(self, keycode: int, flags: int) -> None:
        if keycode != self.keycode:
            return
        pressed = bool(flags & self.flagmask)
        with self._lock:
            if pressed and not self._down:
                self._down = True
                self._holding = False
                if self._hold_timer:
                    self._hold_timer.cancel()
                self._hold_timer = threading.Timer(self.hold_threshold, self._hold_fired)
                self._hold_timer.daemon = True
                self._hold_timer.start()
            elif not pressed and self._down:
                self._down = False
                if self._hold_timer:
                    self._hold_timer.cancel()
                    self._hold_timer = None
                if self._holding:
                    self._holding = False
                    self._fire(self.on_hold_end)
                else:
                    now = time.time()
                    if now - self._last_tap <= self.double_tap_window:
                        self._last_tap = 0.0
                        self._fire(self.on_double_tap)
                    else:
                        self._last_tap = now

    def _hold_fired(self):
        with self._lock:
            if not self._down:
                return
            self._holding = True
        self._fire(self.on_hold_start)

    @staticmethod
    def _fire(cb):
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception("hotkey callback failed")


class HotkeyManager:
    def __init__(self, config):
        self.config = config
        self._monitors: list = []
        self._gestures: list[_KeyGesture] = []
        # callbacks wired by the controller
        self.on_dictation_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_start: Optional[Callable[[], None]] = None
        self.on_ptt_stop: Optional[Callable[[], None]] = None
        self.on_command_start: Optional[Callable[[], None]] = None
        self.on_command_stop: Optional[Callable[[], None]] = None

    def _build_gestures(self):
        window = float(self.config.get("hotkeys.double_tap_window_ms", 400)) / 1000.0
        hold = float(self.config.get("hotkeys.hold_threshold_ms", 350)) / 1000.0
        dictation_key = str(self.config.get("hotkeys.dictation_key", "fn"))
        command_key = str(self.config.get("hotkeys.command_key", "right_option"))
        gestures = [
            _KeyGesture(
                dictation_key,
                window,
                hold,
                on_double_tap=lambda: self._safe(self.on_dictation_toggle),
                on_hold_start=lambda: self._safe(self.on_ptt_start),
                on_hold_end=lambda: self._safe(self.on_ptt_stop),
            )
        ]
        if command_key and command_key.lower() != "none" and command_key != dictation_key:
            gestures.append(
                _KeyGesture(
                    command_key,
                    window,
                    hold,
                    on_hold_start=lambda: self._safe(self.on_command_start),
                    on_hold_end=lambda: self._safe(self.on_command_stop),
                )
            )
        return gestures

    @staticmethod
    def _safe(cb):
        if cb:
            cb()

    # Must be called on the main thread after the app is running.
    def start(self) -> None:
        from AppKit import NSEvent, NSEventMaskFlagsChanged

        self.stop()
        try:
            self._gestures = self._build_gestures()
        except ValueError as e:
            log.error("hotkey config error: %s", e)
            self._gestures = []
            return

        def _handler(event):
            try:
                keycode = event.keyCode()
                flags = int(event.modifierFlags())
                for g in self._gestures:
                    g.handle_flags_changed(keycode, flags)
            except Exception:
                log.exception("hotkey handler error")

        def _local_handler(event):
            _handler(event)
            return event

        m1 = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, _handler
        )
        m2 = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, _local_handler
        )
        self._monitors = [m for m in (m1, m2) if m is not None]
        log.info(
            "hotkeys active: dictation=%s (double-tap toggle / hold PTT), command=%s (hold)",
            self.config.get("hotkeys.dictation_key"),
            self.config.get("hotkeys.command_key"),
        )

    def stop(self) -> None:
        if not self._monitors:
            return
        from AppKit import NSEvent

        for m in self._monitors:
            try:
                NSEvent.removeMonitor_(m)
            except Exception:
                pass
        self._monitors = []

    def reconfigure(self) -> None:
        """Called on config hot-reload (must run on main thread)."""
        self.start()
