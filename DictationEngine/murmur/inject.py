"""Text delivery into the focused app.

Preferred path: write to NSPasteboard, simulate Cmd+V with CGEvent, then
restore the previous pasteboard contents. Fallback path: type the text
directly via CGEventKeyboardSetUnicodeString (slower, but works in apps
that block programmatic paste).

Also provides selection capture (simulated Cmd+C) for Command Mode, and the
frontmost-app bundle id used for per-app styles.
"""

import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

_KVK_ANSI_V = 9
_KVK_ANSI_C = 8


def run_on_main(fn, timeout: float = 1.5):
    """Run `fn` on the AppKit main thread and return its result. AppKit is
    not guaranteed thread-safe, and some callers (hold-to-talk fires from a
    Timer thread) are not on the main thread. Falls back to calling directly
    if there is no running main run loop (tests, headless scripts)."""
    try:
        from Foundation import NSThread

        if NSThread.isMainThread():
            return fn()
    except Exception:
        return fn()

    from PyObjCTools import AppHelper

    done = threading.Event()
    box: dict = {}

    def _run():
        try:
            box["v"] = fn()
        except Exception as e:  # surface the real error to the caller
            box["e"] = e
        done.set()

    try:
        AppHelper.callAfter(_run)
    except Exception:
        return fn()
    if not done.wait(timeout):
        log.warning("main-thread dispatch timed out; running directly")
        return fn()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def frontmost_bundle_id() -> Optional[str]:
    def _get():
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.bundleIdentifier()) if app else None

    try:
        return run_on_main(_get)
    except Exception:
        return None


def frontmost_app_info() -> tuple:
    """(bundle_id, pid) of the frontmost application, or (None, None)."""

    def _get():
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return (None, None)
        return (str(app.bundleIdentifier()), int(app.processIdentifier()))

    try:
        return run_on_main(_get)
    except Exception:
        return (None, None)


OWN_BUNDLE_ID = "com.arsen.murmur"  # Murmur.app's stable identity


def own_bundle_id() -> Optional[str]:
    """Our own app identity as LaunchServices sees it (com.arsen.murmur when
    launched via Murmur.app; None for bare-terminal runs). Never a valid
    paste target. NSRunningApplication.currentApplication() is used because
    the bundle stub execs the venv python, so NSBundle.mainBundle() would
    report nothing."""
    try:
        from AppKit import NSRunningApplication

        bid = NSRunningApplication.currentApplication().bundleIdentifier()
        return str(bid) if bid else None
    except Exception:
        return None


def accessibility_trusted() -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False


class _PasteboardSnapshot:
    """Best-effort save/restore of all pasteboard items and types."""

    def __init__(self):
        from AppKit import NSPasteboard

        pb = NSPasteboard.generalPasteboard()
        self.items = []
        for item in pb.pasteboardItems() or []:
            entry = []
            for t in item.types() or []:
                data = item.dataForType_(t)
                if data is not None:
                    entry.append((t, data))
            if entry:
                self.items.append(entry)

    def restore(self):
        from AppKit import NSPasteboard, NSPasteboardItem

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        new_items = []
        for entry in self.items:
            item = NSPasteboardItem.alloc().init()
            for t, data in entry:
                item.setData_forType_(data, t)
            new_items.append(item)
        if new_items:
            pb.writeObjects_(new_items)


def _post_key_combo(keycode: int, command: bool = True):
    import Quartz

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, keycode, False)
    if command:
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.01)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


class TextInjector:
    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()

    # -- insertion ---------------------------------------------------------
    def insert(self, text: str, target_bundle: Optional[str] = None,
               target_pid: Optional[int] = None) -> str:
        """Insert text into the focused app. Returns a status string:
          "inserted"  - text delivered
          "wrong_app" - the app that dictation started in is no longer
                        frontmost and could not be re-activated; nothing was
                        pasted (caller should fall back to the clipboard)
          "failed"    - paste/type mechanics failed (permissions etc.)

        When `target_bundle` is given (and insertion.follow_focus is false),
        the text is only ever delivered to that app: if the user switched
        windows during transcription we re-activate the original app and wait
        for it to actually become frontmost before posting Cmd+V.
        Pasteboard/CGEvent work runs on the AppKit main thread."""
        if not text:
            return "inserted"
        follow_focus = bool(self.config.get("insertion.follow_focus", False))
        if target_bundle and not follow_focus:
            if target_bundle in (own_bundle_id(), OWN_BUNDLE_ID):
                # dictation started while Murmur itself (settings window /
                # menu) was frontmost — pasting into ourselves is never useful
                log.warning("insert target is Murmur itself (%s); refusing", target_bundle)
                return "wrong_app"
            if not self._ensure_frontmost(target_bundle, target_pid):
                log.warning(
                    "target app %s is not frontmost (now: %s) and could not be "
                    "re-activated; refusing to paste into the wrong app",
                    target_bundle,
                    frontmost_bundle_id(),
                )
                return "wrong_app"
        mode = self.config.get("insertion.mode", "paste")
        try:
            if mode == "type":
                ok = bool(run_on_main(lambda: self._insert_by_typing(text), timeout=10))
            else:
                ok = bool(run_on_main(lambda: self._insert_by_paste(text), timeout=5))
        except Exception:
            log.exception("text insertion failed")
            return "failed"
        if ok:
            log.info(
                "inserted %d chars into %s via %s",
                len(text),
                target_bundle or frontmost_bundle_id() or "unknown app",
                mode,
            )
            return "inserted"
        return "failed"

    def _ensure_frontmost(self, bundle: str, pid: Optional[int],
                          timeout: float = 1.0) -> bool:
        """Make sure `bundle` is the frontmost app, re-activating it if the
        user switched away during transcription. Polls until it actually is
        frontmost (activation is asynchronous) or the timeout passes."""
        if frontmost_bundle_id() == bundle:
            return True

        def _activate():
            from AppKit import NSRunningApplication

            app = None
            if pid:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None and bundle:
                apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
                app = apps[0] if apps and len(apps) else None
            if app is None or app.isTerminated():
                return False
            try:
                from AppKit import NSApplicationActivateIgnoringOtherApps

                opts = NSApplicationActivateIgnoringOtherApps
            except ImportError:
                opts = 1 << 1
            return bool(app.activateWithOptions_(opts))

        try:
            activated = run_on_main(_activate)
        except Exception:
            log.exception("re-activation dispatch failed")
            return False
        if not activated:
            return False
        log.info("re-activated %s to deliver dictation", bundle)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if frontmost_bundle_id() == bundle:
                time.sleep(0.08)  # let key-window focus settle after activation
                return True
            time.sleep(0.05)
        return False

    def copy_to_clipboard(self, text: str) -> bool:
        """Plain pasteboard write with NO restore — used by 'Copy Last
        Transcription', where keeping the text on the clipboard is the point."""

        def _write():
            from AppKit import NSPasteboard, NSPasteboardTypeString

            with self._lock:
                pb = NSPasteboard.generalPasteboard()
                pb.clearContents()
                return bool(pb.setString_forType_(text, NSPasteboardTypeString))

        return bool(run_on_main(_write))

    def _insert_by_paste(self, text: str) -> bool:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        with self._lock:
            # keep_on_clipboard leaves the transcript on the clipboard after
            # pasting (skips the restore of the previous contents).
            restore = self.config.get("insertion.restore_clipboard", True) and not self.config.get(
                "insertion.keep_on_clipboard", False
            )
            snapshot = _PasteboardSnapshot() if restore else None
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            ok = pb.setString_forType_(text, NSPasteboardTypeString)
            if not ok:
                log.error("failed to write pasteboard")
                return False
            delay = float(self.config.get("insertion.paste_delay_ms", 80)) / 1000.0
            time.sleep(delay)
            _post_key_combo(_KVK_ANSI_V, command=True)
            if snapshot is not None:
                # Give the target app time to actually read the pasteboard
                # before we put the old contents back (restore happens on the
                # main thread too).
                def _restore():
                    time.sleep(0.6)
                    try:
                        run_on_main(snapshot.restore)
                    except Exception:
                        log.debug("pasteboard restore failed", exc_info=True)

                threading.Thread(target=_restore, daemon=True).start()
        return True

    def _insert_by_typing(self, text: str) -> bool:
        import Quartz

        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        # CGEventKeyboardSetUnicodeString handles ≤20 UTF-16 units reliably.
        units = list(text)
        chunk: list[str] = []

        def flush():
            if not chunk:
                return
            s = "".join(chunk)
            down = Quartz.CGEventCreateKeyboardEvent(src, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(down, len(s.encode("utf-16-le")) // 2, s)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(src, 0, False)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.008)
            chunk.clear()

        for ch in units:
            chunk.append(ch)
            if len(chunk) >= 16 or ch == "\n":
                flush()
        flush()
        return True

    # -- selection capture (Command Mode) -----------------------------------
    def copy_selection(self, timeout: float = 0.8) -> Optional[str]:
        """Simulate Cmd+C and return the selected text, restoring the user's
        clipboard afterwards. Returns None if nothing was copied. Runs on the
        main thread (briefly blocks the run loop — bounded by `timeout`)."""
        return run_on_main(lambda: self._copy_selection_impl(timeout), timeout=timeout + 1.5)

    def _copy_selection_impl(self, timeout: float = 0.8) -> Optional[str]:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        with self._lock:
            snapshot = _PasteboardSnapshot()
            pb = NSPasteboard.generalPasteboard()
            before = pb.changeCount()
            _post_key_combo(_KVK_ANSI_C, command=True)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if pb.changeCount() != before:
                    break
                time.sleep(0.03)
            text = None
            if pb.changeCount() != before:
                text = pb.stringForType_(NSPasteboardTypeString)
                text = str(text) if text else None
            try:
                snapshot.restore()
            except Exception:
                log.debug("pasteboard restore failed", exc_info=True)
            return text
