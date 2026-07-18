"""Menu bar UI (rumps): state glyph, enable toggle, correction / dictionary
dialogs, config shortcuts. The heavy lifting lives in Controller."""

import logging
import subprocess
import webbrowser

import rumps

from . import APP_NAME
from .controller import Controller, State
from .paths import CONFIG_PATH, LOG_PATH

log = logging.getLogger(__name__)

GLYPHS = {
    State.IDLE: "○",
    State.LISTENING: "●",
    State.COMMAND: "◆",
    State.PROCESSING: "◐",
}
GLYPH_DISABLED = "⊘"


def _notify(title: str, message: str):
    try:
        rumps.notification(title, "", message)
    except Exception:
        # unbundled python can't always post notifications; log instead
        log.info("notification: %s — %s", title, message)


class MurmurMenuBar(rumps.App):
    def __init__(self, config, controller: Controller, hotkeys):
        super().__init__(APP_NAME, title=GLYPHS[State.IDLE], quit_button=None)
        self.config = config
        self.controller = controller
        self.hotkeys = hotkeys
        self._started = False

        from .settings_gui import SettingsGUI

        self.settings_gui = SettingsGUI(config, controller)
        controller.open_settings_gui = self.settings_gui.open

        self.item_enabled = rumps.MenuItem("Enabled", callback=self._toggle_enabled)
        self.item_enabled.state = True
        self.item_overlay_visible = rumps.MenuItem(
            "Show Status Pill", callback=self._toggle_overlay
        )
        self.item_overlay_visible.state = self.controller.overlay.is_always_visible()
        self.item_status = rumps.MenuItem("…", callback=None)
        self.item_hint = rumps.MenuItem(self._hotkey_hint(), callback=None)
        # permission warnings: hidden (callback-less no-ops) when all is well
        self.item_perm_ax = rumps.MenuItem("Grant Accessibility…", callback=self._grant_ax)
        self.item_perm_mic = rumps.MenuItem("Grant Microphone…", callback=self._grant_mic)

        self.menu = [
            self.item_enabled,
            self.item_overlay_visible,
            None,
            self.item_status,
            self.item_hint,
            self.item_perm_ax,
            self.item_perm_mic,
            None,
            rumps.MenuItem("Copy Last Transcription", callback=self._copy_last),
            rumps.MenuItem("Fix Last Transcription…", callback=self._fix_last),
            rumps.MenuItem("Add Dictionary Term…", callback=self._add_term),
            None,
            rumps.MenuItem("Settings…", callback=self._open_settings),
            rumps.MenuItem("Edit Config File", callback=self._open_config),
            rumps.MenuItem("Open Log", callback=self._open_log),
            None,
            rumps.MenuItem("Quit Murmur", callback=self._quit),
        ]

        controller.on_state_change = self._on_state
        controller.notify = _notify
        # the pill's right-click menu needs a way to quit the app
        controller.overlay.callbacks["quit"] = lambda: rumps.quit_application()

        # one-shot startup hook: NSEvent monitors need a running app
        self._boot_timer = rumps.Timer(self._on_boot, 0.4)
        self._boot_timer.start()
        self._status_timer = rumps.Timer(self._refresh_status, 7)
        self._status_timer.start()

    # ---- lifecycle -----------------------------------------------------
    def _on_boot(self, timer):
        if self._started:
            timer.stop()
            return
        self._started = True
        timer.stop()
        try:
            self.hotkeys.start()
        except Exception:
            log.exception("failed to start hotkeys")
        self.controller.startup()
        self._refresh_status(None)
        if getattr(self, "first_run", False):
            d = self.config.get("hotkeys.dictation_key", "fn")
            _notify(
                "Welcome to Murmur",
                f"Look for the small dot at the bottom of your screen — click it "
                f"(or double-tap {d}) to dictate. Settings live in this menu-bar "
                f"icon under Settings…",
            )

    def _quit(self, _):
        rumps.quit_application()

    # ---- state / status ---------------------------------------------------
    def _on_state(self, state: State):
        from PyObjCTools import AppHelper

        def _apply():
            self.title = GLYPHS.get(state, "○") if self.controller.enabled else GLYPH_DISABLED

        AppHelper.callAfter(_apply)

    def _refresh_status(self, _):
        try:
            self.item_status.title = self.controller.status_line()
            self.item_hint.title = self._hotkey_hint()
            # keep the checkbox honest if the pill was hidden from its own
            # right-click menu rather than from here
            self.item_overlay_visible.state = self.controller.overlay.is_always_visible()
            self._refresh_permission_items()
        except Exception:
            pass

    def _refresh_permission_items(self):
        from .inject import accessibility_trusted

        ax_ok = accessibility_trusted()
        mic_err = self.controller.audio.last_error
        self.item_perm_ax.title = (
            "Grant Accessibility… (hotkeys & paste disabled)" if not ax_ok else "Permissions: OK"
        )
        self.item_perm_ax.set_callback(self._grant_ax if not ax_ok else None)
        if mic_err:
            self.item_perm_mic.title = "Grant Microphone… (mic unavailable)"
            self.item_perm_mic.set_callback(self._grant_mic)
        else:
            self.item_perm_mic.title = "Microphone: OK"
            self.item_perm_mic.set_callback(None)

    def _grant_ax(self, _):
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"]
        )

    def _grant_mic(self, _):
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"]
        )
        # a retry costs nothing and picks the permission up once granted
        self.controller.audio.ensure_stream()

    def _open_settings(self, _):
        self.settings_gui.open()

    def _hotkey_hint(self) -> str:
        d = self.config.get("hotkeys.dictation_key", "fn")
        c = self.config.get("hotkeys.command_key", "right_option")
        return f"Double-tap {d}: dictate · hold {c}: command"

    def _toggle_enabled(self, item):
        self.controller.enabled = not self.controller.enabled
        item.state = self.controller.enabled
        if not self.controller.enabled:
            self.controller.cancel()
        self.title = GLYPHS[State.IDLE] if self.controller.enabled else GLYPH_DISABLED

    def _toggle_overlay(self, item):
        new_val = self.controller.toggle_overlay_visible()
        item.state = new_val

    def _copy_last(self, _):
        if self.controller.copy_last_transcription():
            _notify(APP_NAME, "Copied last transcription to clipboard.")
        else:
            _notify(APP_NAME, "Nothing transcribed yet.")

    # ---- dialogs ---------------------------------------------------------
    def _fix_last(self, _):
        current = self.controller.last_final
        if not current:
            _notify(APP_NAME, "Nothing transcribed yet.")
            return
        win = rumps.Window(
            message="Edit the last transcription. Murmur learns replacements "
            "and new terms from your fix.",
            title="Fix Last Transcription",
            default_text=current,
            ok="Learn",
            cancel="Cancel",
            dimensions=(420, 120),
        )
        resp = win.run()
        if resp.clicked and resp.text.strip() and resp.text != current:
            learned = self.controller.record_correction(resp.text.strip())
            if learned:
                pairs = ", ".join(f"{s} → {d}" for s, d in learned)
                _notify(APP_NAME, f"Learned: {pairs}")
            else:
                _notify(APP_NAME, "Correction saved.")

    def _add_term(self, _):
        win = rumps.Window(
            message="Add a name, product, or jargon term (one per line). It will "
            "bias recognition and be protected in output.",
            title="Add Dictionary Term",
            default_text="",
            ok="Add",
            cancel="Cancel",
            dimensions=(320, 80),
        )
        resp = win.run()
        if resp.clicked and resp.text.strip():
            for line in resp.text.strip().splitlines():
                self.controller.dictionary.add_term(line)
            _notify(APP_NAME, "Dictionary updated.")

    # ---- files -----------------------------------------------------------
    def _open_config(self, _):
        subprocess.Popen(["open", CONFIG_PATH])

    def _open_log(self, _):
        subprocess.Popen(["open", "-a", "Console", LOG_PATH])
