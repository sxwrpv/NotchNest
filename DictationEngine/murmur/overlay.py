"""Interactive status pill: a single borderless, NON-ACTIVATING NSPanel that
is the on-screen counterpart to the menu-bar icon.

Visual states (unchanged from the always-visible design):
  - idle:       small dim dot; expands on hover to reveal mini controls
  - listening:  full pill with live multi-bar waveform + partial transcript
  - command:    same as listening (Command Mode is also a capture session)
  - processing: full pill with pulsing status dot while the LLM runs
  - error:      full pill, red dot + message, auto-reverts

Interactivity (all while never taking key/main status or activating the app —
NSWindowStyleMaskNonactivatingPanel is load-bearing: the frontmost app keeps
keyboard focus so paste still lands in the right place):
  - left-click on the pill body   -> toggle dictation (same path as hotkey)
  - hover                         -> mini controls: style-switcher chip and,
                                     during a session, a cancel (✕) button
  - drag (>4px threshold)         -> reposition; anchor persisted to
                                     config.yaml (overlay.position)
  - right-click                   -> quick menu: Open Settings / Hide Pill /
                                     Quit Murmur

Positioning: one anchor scheme for every size/state — anchor.x = horizontal
center of the pill, anchor.y = its bottom edge (global AppKit coords, origin
bottom-left). Default (position: null) = bottom-center of the screen under
the mouse. All AppKit calls happen on the main thread; the public API is
thread-safe and marshals via AppHelper.callAfter.
"""

import logging
import math
import threading
import time
from typing import Callable, Optional

from .config import write_overlay_position

log = logging.getLogger(__name__)

_ACTIVE_MODES = {"listening", "command", "processing", "error"}
_SESSION_MODES = {"listening", "command", "processing"}  # cancel is meaningful
_DRAG_THRESHOLD = 4.0  # px of movement before a press becomes a drag

# ObjC classes are registered globally by name, so they must be created only
# once per process even if several Overlay instances exist (e.g. in tests).
_OBJC_CLASSES: dict = {}


class Overlay:
    IDLE_SIZE = (28.0, 28.0)
    IDLE_HOVER_SIZE = (190.0, 34.0)
    ACTIVE_HEIGHT = 52.0

    def __init__(self, config, level_provider: Optional[Callable[[], list]] = None):
        self.config = config
        self.level_provider = level_provider or (lambda: [])
        # Wired by the controller / menu bar:
        #   toggle()        -> start/stop dictation (same as hotkey toggle)
        #   cancel()        -> abort session / discard in-flight result
        #   cycle_style()   -> advance style override, returns new label
        #   style_label()   -> current label for the chip ("Auto", "Formal"…)
        #   open_settings() -> open config.yaml in the default editor
        #   hide()          -> same as menu-bar Show Status Pill off
        #   quit()          -> quit the app
        self.callbacks: dict = {}
        self._panel = None
        self._view = None
        self._timer = None
        self._menu_target = None
        self._lock = threading.Lock()
        self._mode: Optional[str] = None
        self._text: str = ""
        self._visible = False
        self._geom_key = None  # (category, hovered) the current frame matches
        self._always_visible = bool(config.get("overlay.always_visible", True))
        # hover state (hysteresis: expand instantly, collapse after a delay —
        # the expanded pill contains the collapsed dot's area, so the pointer
        # stays inside on expand and there's no enter/exit oscillation)
        self._hovered = False
        self._unhover_timer: Optional[threading.Timer] = None
        # drag state
        self._press_screen: Optional[tuple] = None  # mouse-down (screen coords)
        self._press_origin: Optional[tuple] = None  # panel origin at mouse-down
        self._dragging = False
        self._session_anchor: Optional[tuple] = None  # post-drag, pre-reload
        # hit regions for mini controls, in view coords: name -> (x, y, w, h)
        self._hit_rects: dict = {}

    # ---- public, thread-safe API ----------------------------------------
    def start(self) -> None:
        with self._lock:
            self._always_visible = bool(self.config.get("overlay.always_visible", True))
            av = self._always_visible
        if self.config.get("overlay.enabled", True) and av:
            self.show("idle")

    def is_always_visible(self) -> bool:
        with self._lock:
            return self._always_visible

    def set_always_visible(self, value: bool) -> None:
        value = bool(value)
        with self._lock:
            self._always_visible = value
            mode = self._mode
        if value:
            self.show(mode or "idle")
        else:
            self.hide()

    def show(self, mode: str, text: str = "") -> None:
        if not self.config.get("overlay.enabled", True):
            return
        with self._lock:
            self._mode, self._text = mode, text
        self._call(self._apply_main)

    def set_text(self, text: str) -> None:
        with self._lock:
            self._text = text

    def set_mode(self, mode: str, text: Optional[str] = None) -> None:
        with self._lock:
            self._mode = mode
            if text is not None:
                self._text = text
        self._call(self._apply_main)

    def hide(self) -> None:
        with self._lock:
            self._mode = None
        self._call(self._hide_main)

    def end_session(self) -> None:
        if self.is_always_visible():
            self.show("idle")
        else:
            self.hide()

    def flash_error(self, message: str, seconds: float = 2.5) -> None:
        self.show("error", message)
        t = threading.Timer(seconds, self.end_session)
        t.daemon = True
        t.start()

    def refresh_position(self) -> None:
        """Config hot-reload hook: re-apply the (possibly edited) anchor."""
        self._session_anchor = None
        self._call(self._reapply_frame)

    def state(self) -> tuple:
        with self._lock:
            return self._mode, self._text

    @staticmethod
    def _call(fn):
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn)
        except Exception:
            log.debug("overlay dispatch failed", exc_info=True)

    def _cb(self, name, *args):
        fn = self.callbacks.get(name)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception:
            log.exception("overlay callback %r failed", name)
            return None

    # ---- hover (with hysteresis) -------------------------------------------
    def _on_mouse_entered(self):
        if self._unhover_timer:
            self._unhover_timer.cancel()
            self._unhover_timer = None
        if not self._hovered:
            self._hovered = True
            self._call(self._apply_main)

    def _on_mouse_exited(self):
        if self._unhover_timer:
            self._unhover_timer.cancel()

        def _collapse():
            self._unhover_timer = None
            self._hovered = False
            self._call(self._apply_main)

        # delay the collapse so brushing the edge doesn't flicker the pill
        self._unhover_timer = threading.Timer(0.35, _collapse)
        self._unhover_timer.daemon = True
        self._unhover_timer.start()

    # ---- click / drag / menu (called from the view, main thread) -------------
    def _mouse_down(self, screen_point):
        self._press_screen = screen_point
        self._dragging = False
        if self._panel is not None:
            o = self._panel.frame().origin
            self._press_origin = (o.x, o.y)

    def _mouse_dragged(self, screen_point):
        if self._press_screen is None or self._panel is None:
            return
        dx = screen_point[0] - self._press_screen[0]
        dy = screen_point[1] - self._press_screen[1]
        if not self._dragging and (dx * dx + dy * dy) < _DRAG_THRESHOLD**2:
            return
        self._dragging = True
        self._panel.setFrameOrigin_(
            (self._press_origin[0] + dx, self._press_origin[1] + dy)
        )

    def _mouse_up(self, view_point):
        try:
            if self._dragging:
                self._finish_drag()
                return
            # plain click: route by hit region
            mode, _ = self.state()
            if self._hit(view_point, "cancel") and mode in _SESSION_MODES:
                self._cb("cancel")
            elif self._hit(view_point, "style"):
                self._cb("cycle_style")
                if self._view is not None:
                    self._view.setNeedsDisplay_(True)
            else:
                self._cb("toggle")
        finally:
            self._press_screen = None
            self._press_origin = None
            self._dragging = False

    def _hit(self, point, name) -> bool:
        r = self._hit_rects.get(name)
        if not r:
            return False
        x, y, w, h = r
        return x <= point[0] <= x + w and y <= point[1] <= y + h

    def _finish_drag(self):
        f = self._panel.frame()
        anchor = (f.origin.x + f.size.width / 2.0, f.origin.y)
        self._session_anchor = anchor
        ok = write_overlay_position(self.config.path, anchor[0], anchor[1])
        log.info(
            "pill moved to anchor (%.0f, %.0f)%s",
            anchor[0],
            anchor[1],
            "" if ok else " (config write FAILED — position is session-only)",
        )

    def _right_mouse_down(self, event):
        try:
            from AppKit import NSMenu, NSMenuItem

            if self._menu_target is None:
                self._menu_target = _make_menu_target(self)
            menu = NSMenu.alloc().initWithTitle_("Murmur")
            # manual enable/disable (for the greyed-out copy item)
            menu.setAutoenablesItems_(False)

            copy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Copy Last Transcription", "murmurCopyLast:", ""
            )
            copy_item.setTarget_(self._menu_target)
            copy_item.setEnabled_(bool(self._cb("has_last")))
            menu.addItem_(copy_item)
            menu.addItem_(NSMenuItem.separatorItem())

            for title, sel in (
                ("Settings…", "murmurOpenSettings:"),
                ("Hide Pill", "murmurHidePill:"),
                ("Quit Murmur", "murmurQuit:"),
            ):
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, sel, ""
                )
                item.setTarget_(self._menu_target)
                item.setEnabled_(True)
                menu.addItem_(item)
            NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self._view)
        except Exception:
            log.exception("pill context menu failed")

    # ---- main-thread internals -------------------------------------------
    def _ensure_panel(self):
        if self._panel is not None:
            return
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSPanel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
            NSStatusWindowLevel,
        )
        from Foundation import NSMakeRect

        if "PillView" not in _OBJC_CLASSES:
            _OBJC_CLASSES["PillView"] = _define_pill_view_class()
        PillView = _OBJC_CLASSES["PillView"]

        width = float(self.config.get("overlay.width", 400))
        rect = NSMakeRect(0, 0, width, self.ACTIVE_HEIGHT)
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        # Interactive pill: the panel now receives mouse events (accepted
        # tradeoff — it is no longer click-through), but it must NEVER
        # activate us or steal key status from the frontmost app. The
        # NonactivatingPanel style mask plus becomesKeyOnlyIfNeeded guarantee
        # that; we never call makeKeyWindow/activateIgnoringOtherApps.
        panel.setIgnoresMouseEvents_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        view = PillView.alloc().initWithFrame_(rect)
        view.murmur_overlay = self
        panel.setContentView_(view)
        self._panel, self._view = panel, view

    def _frame_size_for_mode(self, mode: str, hovered: bool) -> tuple:
        if mode in (None, "idle"):
            return self.IDLE_HOVER_SIZE if hovered else self.IDLE_SIZE
        width = float(self.config.get("overlay.width", 400))
        return (width, self.ACTIVE_HEIGHT)

    def _anchor(self) -> Optional[tuple]:
        """Anchor precedence: this-session drag > config > None (default)."""
        if self._session_anchor is not None:
            return self._session_anchor
        pos = self.config.get("overlay.position")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            try:
                return (float(pos["x"]), float(pos["y"]))
            except (TypeError, ValueError):
                return None
        return None

    def _target_frame(self, w: float, h: float):
        """One positioning formula for every pill size: place the frame so
        (center-x, bottom-y) lands on the anchor, clamped on-screen. No
        anchor -> bottom-center of the screen under the mouse."""
        from AppKit import NSEvent, NSScreen
        from Foundation import NSMakeRect, NSPointInRect

        anchor = self._anchor()
        screen = None
        try:
            probe = anchor if anchor else tuple(NSEvent.mouseLocation())
            for s in NSScreen.screens():
                if NSPointInRect(probe, s.frame()):
                    screen = s
                    break
        except Exception:
            pass
        screen = screen or NSScreen.mainScreen()
        if screen is None:
            return NSMakeRect(100, 100, w, h)
        f = screen.visibleFrame()
        if anchor:
            x = anchor[0] - w / 2.0
            y = anchor[1]
        else:
            x = f.origin.x + (f.size.width - w) / 2.0
            y = f.origin.y + 24.0
        # clamp fully on-screen
        x = max(f.origin.x, min(x, f.origin.x + f.size.width - w))
        y = max(f.origin.y, min(y, f.origin.y + f.size.height - h))
        return NSMakeRect(x, y, w, h)

    def _apply_frame_for_mode(self, mode: str, hovered: bool):
        w, h = self._frame_size_for_mode(mode, hovered)
        frame = self._target_frame(w, h)
        self._panel.setFrame_display_(frame, True)
        from Foundation import NSMakeRect

        self._view.setFrame_(NSMakeRect(0, 0, w, h))

    def _reapply_frame(self):
        if self._panel is None or not self._visible:
            return
        mode, _ = self.state()
        self._apply_frame_for_mode(mode, self._hovered)
        if self._view is not None:
            self._view.setNeedsDisplay_(True)

    def _apply_main(self):
        try:
            # Single choke point for every "draw the pill" path (show, set_mode,
            # hover handlers): with the overlay disabled in config, never
            # surface the panel — state still flows to the notch bridge.
            if not self.config.get("overlay.enabled", True):
                if self._visible:
                    self._hide_main()
                return
            self._ensure_panel()
            mode, _ = self.state()
            hovered = self._hovered
            category = "idle" if mode in (None, "idle") else "active"
            geom_key = (category, hovered if category == "idle" else False)
            if geom_key != self._geom_key or not self._visible:
                if not self._dragging:  # don't fight an in-progress drag
                    self._apply_frame_for_mode(mode, hovered)
                self._geom_key = geom_key
            if not self._visible:
                self._panel.orderFrontRegardless()
                self._visible = True
            self._configure_timer(mode)
            if self._view is not None:
                self._view.setNeedsDisplay_(True)
        except Exception:
            log.exception("overlay show failed")

    def _hide_main(self):
        try:
            self._stop_timer()
            if self._panel is not None:
                self._panel.orderOut_(None)
            self._visible = False
            self._geom_key = None
        except Exception:
            log.exception("overlay hide failed")

    # ---- redraw timer: rate depends on mode so an idle dot (static) costs
    # ~nothing on this fanless machine, while listening/processing animate.
    def _interval_for_mode(self, mode: str) -> Optional[float]:
        if mode in (None, "idle"):
            return None  # static dot / static hover pill: no periodic redraws
        if mode == "processing":
            return 1.0 / 15.0
        return 1.0 / 30.0

    def _configure_timer(self, mode: str):
        interval = self._interval_for_mode(mode)
        if interval is None:
            self._stop_timer()
            return
        self._start_timer(interval)

    def _start_timer(self, interval: float = 1.0 / 30.0):
        from Foundation import NSTimer, NSRunLoop, NSRunLoopCommonModes

        self._stop_timer()

        def _tick(timer):
            if self._view is not None:
                self._view.setNeedsDisplay_(True)

        self._timer = NSTimer.timerWithTimeInterval_repeats_block_(interval, True, _tick)
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def _stop_timer(self):
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:
                pass
            self._timer = None


def _make_menu_target(overlay: "Overlay"):
    """NSObject subclass instance exposing ObjC selectors for the pill's
    right-click menu items. The class is registered once per process."""
    if "MenuTarget" not in _OBJC_CLASSES:
        from Foundation import NSObject

        class _MurmurMenuTarget(NSObject):
            def murmurCopyLast_(self, sender):
                self.murmur_overlay._cb("copy_last")

            def murmurOpenSettings_(self, sender):
                self.murmur_overlay._cb("open_settings")

            def murmurHidePill_(self, sender):
                self.murmur_overlay._cb("hide")

            def murmurQuit_(self, sender):
                self.murmur_overlay._cb("quit")

        _OBJC_CLASSES["MenuTarget"] = _MurmurMenuTarget
    target = _OBJC_CLASSES["MenuTarget"].alloc().init()
    target.murmur_overlay = overlay
    return target


def _define_pill_view_class():
    """Create the NSView subclass for the pill (once per process — ObjC class
    names are global). Instances find their Overlay via `.murmur_overlay`."""
    import objc
    from AppKit import (
        NSTrackingActiveAlways,
        NSTrackingArea,
        NSTrackingInVisibleRect,
        NSTrackingMouseEnteredAndExited,
        NSView,
    )
    from Foundation import NSMakeRect

    class _MurmurPillView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(_MurmurPillView, self).initWithFrame_(frame)
            if self is not None:
                # InVisibleRect keeps the tracking area glued to the view's
                # bounds across all our resizes.
                area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                    NSMakeRect(0, 0, 0, 0),
                    NSTrackingMouseEnteredAndExited
                    | NSTrackingActiveAlways
                    | NSTrackingInVisibleRect,
                    self,
                    None,
                )
                self.addTrackingArea_(area)
            return self

        def _ov(self):
            return getattr(self, "murmur_overlay", None)

        def isFlipped(self):
            return False

        # Receive the first click without requiring window activation —
        # essential: our window never becomes key/active.
        def acceptsFirstMouse_(self, event):
            return True

        def mouseDownCanMoveWindow(self):
            return False  # we do drag handling ourselves (with threshold)

        def mouseEntered_(self, event):
            ov = self._ov()
            if ov:
                ov._on_mouse_entered()

        def mouseExited_(self, event):
            ov = self._ov()
            if ov:
                ov._on_mouse_exited()

        def mouseDown_(self, event):
            from AppKit import NSEvent

            ov = self._ov()
            if ov:
                ov._mouse_down(tuple(NSEvent.mouseLocation()))

        def mouseDragged_(self, event):
            from AppKit import NSEvent

            ov = self._ov()
            if ov:
                ov._mouse_dragged(tuple(NSEvent.mouseLocation()))

        def mouseUp_(self, event):
            ov = self._ov()
            if ov:
                p = self.convertPoint_fromView_(event.locationInWindow(), None)
                ov._mouse_up((p.x, p.y))

        def rightMouseDown_(self, event):
            ov = self._ov()
            if ov:
                ov._right_mouse_down(event)

        def drawRect_(self, rect):
            from AppKit import (
                NSBezierPath,
                NSColor,
                NSFont,
                NSFontAttributeName,
                NSForegroundColorAttributeName,
            )
            from Foundation import NSMakeRect as MR, NSString

            ov = self._ov()
            if ov is None:
                return
            bounds = self.bounds()
            w, h = bounds.size.width, bounds.size.height
            mode, text = ov.state()
            hovered = ov._hovered
            now = time.time()
            ov._hit_rects = {}

            def draw_text(s, x, y, size=12, white=0.95, alpha=1.0, color=None):
                font = NSFont.systemFontOfSize_(size)
                c = color or NSColor.colorWithCalibratedWhite_alpha_(white, alpha)
                attrs = {
                    NSFontAttributeName: font,
                    NSForegroundColorAttributeName: c,
                }
                NSString.stringWithString_(s).drawAtPoint_withAttributes_((x, y), attrs)

            def measure(s, size=12):
                font = NSFont.systemFontOfSize_(size)
                attrs = {NSFontAttributeName: font}
                return NSString.stringWithString_(s).sizeWithAttributes_(attrs)

            def style_chip(right_edge):
                """Draw the style-switcher chip ending at right_edge.
                Returns the chip's left edge x."""
                label = str(ov._cb("style_label") or "Auto")
                tsz = measure(label, 11)
                cw = tsz.width + 16
                ch = 20.0
                cx = right_edge - cw
                cy = h / 2 - ch / 2
                NSColor.colorWithCalibratedWhite_alpha_(0.35, 0.7).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    MR(cx, cy, cw, ch), ch / 2, ch / 2
                ).fill()
                draw_text(label, cx + 8, cy + (ch - tsz.height) / 2, size=11)
                ov._hit_rects["style"] = (cx, cy, cw, ch)
                return cx

            def cancel_button(center_x):
                r = 10.0
                cy = h / 2
                NSColor.colorWithCalibratedWhite_alpha_(0.35, 0.7).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    MR(center_x - r, cy - r, r * 2, r * 2)
                ).fill()
                tsz = measure("✕", 11)
                draw_text("✕", center_x - tsz.width / 2, cy - tsz.height / 2, size=11)
                ov._hit_rects["cancel"] = (center_x - r, cy - r, r * 2, r * 2)

            # ---- idle -------------------------------------------------
            if mode in (None, "idle"):
                if not hovered:
                    NSColor.colorWithCalibratedWhite_alpha_(0.85, 0.5).setFill()
                    d = min(w, h) * 0.42
                    NSBezierPath.bezierPathWithOvalInRect_(
                        MR((w - d) / 2.0, (h - d) / 2.0, d, d)
                    ).fill()
                    return
                # idle + hover: slim pill with dot, "dictate" hint, chip
                NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.9).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    MR(0, 0, w, h), h / 2, h / 2
                ).fill()
                NSColor.colorWithCalibratedWhite_alpha_(0.85, 0.6).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(MR(12, h / 2 - 5, 10, 10)).fill()
                style_chip(w - 8)
                hint_sz = measure("dictate", 11)
                draw_text(
                    "dictate", 30, (h - hint_sz.height) / 2, size=11, white=0.8, alpha=0.9
                )
                return

            # ---- active pill background --------------------------------
            NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.88).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                MR(0, 0, w, h), h / 2.0, h / 2.0
            ).fill()

            # status dot (pulses while processing)
            dot_colors = {
                "listening": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.26, 0.21, 1),
                "command": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.30, 0.55, 0.95, 1),
                "processing": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.65, 0.15, 1),
                "error": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.85, 0.20, 0.20, 1),
            }
            dot_colors.get(mode or "", NSColor.grayColor()).setFill()
            if mode == "processing":
                pulse = 0.5 + 0.5 * math.sin(now * 5.0)
                r = 4.0 + pulse * 2.5
            else:
                r = 5.0
            NSBezierPath.bezierPathWithOvalInRect_(
                MR(21 - r, h / 2 - r, r * 2, r * 2)
            ).fill()

            # hover mini-controls (right side); reserve their width so the
            # transcript text doesn't run underneath them
            right_reserved = 18.0
            if hovered:
                if mode in _SESSION_MODES:
                    cancel_cx = w - 22.0
                    cancel_button(cancel_cx)
                    chip_left = style_chip(cancel_cx - 16)
                else:
                    chip_left = style_chip(w - 12)
                right_reserved = w - chip_left + 10

            # waveform (listening / command)
            bars_x, bars_w = 36.0, 150.0
            if mode in ("listening", "command"):
                n = 40
                levels = list(ov.level_provider() or [])[-n:]
                levels = [0.0] * (n - len(levels)) + levels
                bw = bars_w / n
                NSColor.colorWithCalibratedWhite_alpha_(0.95, 0.95).setFill()
                max_bar_h = h - 14
                for i, lv in enumerate(levels):
                    jitter = 0.035 + 0.03 * math.sin(i * 0.9 + now * 7.0)
                    amp = max(min(1.0, lv), jitter)
                    bh = max(3.0, amp * max_bar_h)
                    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        MR(bars_x + i * bw + 0.6, h / 2 - bh / 2, bw - 1.2, bh),
                        (bw - 1.2) / 2.0,
                        (bw - 1.2) / 2.0,
                    ).fill()
                text_x = bars_x + bars_w + 12
            else:
                text_x = 36.0

            # transcript / status text (tail-ellipsized)
            display = text or {
                "listening": "Listening…",
                "command": "Command… (speak an instruction)",
                "processing": "Processing…",
            }.get(mode or "", "")
            font = NSFont.systemFontOfSize_(13)
            color = (
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.55, 0.5, 1)
                if mode == "error"
                else NSColor.colorWithCalibratedWhite_alpha_(0.95, 1)
            )
            attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
            avail = w - text_x - right_reserved
            if avail > 20 and display:
                s = display.replace("\n", " ")
                ns = NSString.stringWithString_(s)
                while ns.sizeWithAttributes_(attrs).width > avail and len(s) > 4:
                    s = "…" + s[max(4, len(s) // 8):].lstrip("…")
                    ns = NSString.stringWithString_(s)
                size = ns.sizeWithAttributes_(attrs)
                ns.drawAtPoint_withAttributes_((text_x, (h - size.height) / 2.0), attrs)

    return _MurmurPillView
