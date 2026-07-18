"""Native AppKit Settings window (pyobjc).

config.yaml remains the single source of truth: the window reads the merged
config and writes only the keys the user changed, via the comment-preserving
writer in config.py. The running app picks changes up through the existing
hot-reload watcher, so nothing needs a restart. Unlike the status pill, this
is a normal activating window — it may take focus.

Tabs: General / Overlay / Dictionary / Snippets / Styles / Advanced.
"""

import logging
import subprocess
import threading

from .config import write_config_values
from .hotkeys import KEYS

log = logging.getLogger(__name__)

_OBJC = {}  # ObjC classes are registered globally; create once per process

LANG_CHOICES = ["auto", "en", "ru", "de", "fr", "es", "uk"]
MODEL_CHOICES = [
    "large-v3-turbo-q4",
    "large-v3-turbo",
    "medium",
    "small",
    "small.en",
    "base.en",
    "tiny.en",
]
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
BACKENDS = ["auto", "ollama", "mlx", "none"]


def _get_action_target_class():
    if "ActionTarget" in _OBJC:
        return _OBJC["ActionTarget"]
    from Foundation import NSObject

    class _MurmurSettingsTarget(NSObject):
        # every control routes through here; `gui` is set as a python attr
        def applyClicked_(self, sender):
            self.gui._apply()

        def revertClicked_(self, sender):
            self.gui._populate()

        def editFileClicked_(self, sender):
            self.gui._edit_file()

        def resetPositionClicked_(self, sender):
            self.gui._reset_position()

        def addRow_(self, sender):
            self.gui._table_add(sender.tag())

        def removeRow_(self, sender):
            self.gui._table_remove(sender.tag())

        def windowWillClose_(self, note):
            pass

    _OBJC["ActionTarget"] = _MurmurSettingsTarget
    return _MurmurSettingsTarget


def _get_table_source_class():
    if "TableSource" in _OBJC:
        return _OBJC["TableSource"]
    from Foundation import NSObject

    class _MurmurTableSource(NSObject):
        """Generic editable table backing store: `rows` is a list of dicts
        keyed by column identifier. `on_select(row_index)` optional."""

        def numberOfRowsInTableView_(self, tv):
            return len(getattr(self, "rows", []))

        def tableView_objectValueForTableColumn_row_(self, tv, col, row):
            try:
                return self.rows[row].get(str(col.identifier()), "")
            except Exception:
                return ""

        def tableView_setObjectValue_forTableColumn_row_(self, tv, value, col, row):
            try:
                self.rows[row][str(col.identifier())] = str(value)
            except Exception:
                pass

        def tableView_shouldEditTableColumn_row_(self, tv, col, row):
            return bool(getattr(self, "editable", True))

        def tableViewSelectionDidChange_(self, note):
            cb = getattr(self, "on_select", None)
            if cb:
                try:
                    cb(note.object().selectedRow())
                except Exception:
                    log.exception("table selection callback failed")

    _OBJC["TableSource"] = _MurmurTableSource
    return _MurmurTableSource


class SettingsGUI:
    """Owns the settings NSWindow. Public API is thread-safe."""

    def __init__(self, config, controller):
        self.config = config
        self.controller = controller
        self._window = None
        self._target = None
        self._w = {}  # widget registry
        self._tables = {}  # tag -> (table_view, source)
        self._snippet_bodies = {}  # trigger -> body (model behind the table)
        self._current_snippet_row = -1
        self._lock = threading.Lock()

    # ---- public ----------------------------------------------------------
    def open(self):
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._open_main)

    # ---- window construction (main thread) ---------------------------------
    def _open_main(self):
        try:
            from AppKit import NSApp

            if self._window is None:
                self._build()
            self._populate()
            NSApp.activateIgnoringOtherApps_(True)
            self._window.makeKeyAndOrderFront_(None)
        except Exception:
            log.exception("settings window failed to open")

    def _build(self):
        from AppKit import (
            NSBackingStoreBuffered,
            NSTabView,
            NSTabViewItem,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskTitled,
        )
        from Foundation import NSMakeRect

        self._target = _get_action_target_class().alloc().init()
        self._target.gui = self

        rect = NSMakeRect(0, 0, 660, 500)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("Murmur Settings")
        win.setReleasedWhenClosed_(False)
        win.center()

        content = win.contentView()
        tabs = NSTabView.alloc().initWithFrame_(NSMakeRect(8, 46, 644, 446))
        for ident, label, builder in (
            ("general", "General", self._build_general),
            ("overlay", "Overlay", self._build_overlay),
            ("dictionary", "Dictionary", self._build_dictionary),
            ("snippets", "Snippets", self._build_snippets),
            ("styles", "Styles", self._build_styles),
            ("advanced", "Advanced", self._build_advanced),
        ):
            item = NSTabViewItem.alloc().initWithIdentifier_(ident)
            item.setLabel_(label)
            item.setView_(builder())
            tabs.addTabViewItem_(item)
        content.addSubview_(tabs)

        self._add_button(content, "Edit config file…", 12, 10, 140, "editFileClicked:")
        self._add_button(content, "Revert", 430, 10, 100, "revertClicked:")
        apply_btn = self._add_button(content, "Apply", 540, 10, 100, "applyClicked:")
        apply_btn.setKeyEquivalent_("\r")
        self._window = win

    # ---- widget helpers ------------------------------------------------------
    def _tab_view(self):
        from AppKit import NSView
        from Foundation import NSMakeRect

        return NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 620, 400))

    def _label(self, parent, text, x, y, w=170):
        from AppKit import NSTextField
        from Foundation import NSMakeRect

        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(NSMakeRect(x, y, w, 20))
        lbl.setAlignment_(2)  # right
        parent.addSubview_(lbl)
        return lbl

    def _note(self, parent, text, x, y, w=560):
        from AppKit import NSColor, NSFont, NSTextField
        from Foundation import NSMakeRect

        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(NSMakeRect(x, y, w, 32))
        lbl.setFont_(NSFont.systemFontOfSize_(10))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(lbl)
        return lbl

    def _popup(self, parent, name, choices, x, y, w=200):
        from AppKit import NSPopUpButton
        from Foundation import NSMakeRect

        pop = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(x, y, w, 24), False)
        pop.addItemsWithTitles_(list(choices))
        parent.addSubview_(pop)
        self._w[name] = pop
        return pop

    def _combo(self, parent, name, choices, x, y, w=220):
        from AppKit import NSComboBox
        from Foundation import NSMakeRect

        box = NSComboBox.alloc().initWithFrame_(NSMakeRect(x, y, w, 24))
        box.addItemsWithObjectValues_(list(choices))
        parent.addSubview_(box)
        self._w[name] = box
        return box

    def _check(self, parent, name, title, x, y, w=380):
        from AppKit import NSButton
        from Foundation import NSMakeRect

        btn = NSButton.checkboxWithTitle_target_action_(title, None, None)
        btn.setFrame_(NSMakeRect(x, y, w, 20))
        parent.addSubview_(btn)
        self._w[name] = btn
        return btn

    def _field(self, parent, name, x, y, w=220):
        from AppKit import NSTextField
        from Foundation import NSMakeRect

        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
        parent.addSubview_(f)
        self._w[name] = f
        return f

    def _add_button(self, parent, title, x, y, w, action, tag=0):
        from AppKit import NSButton
        from Foundation import NSMakeRect

        btn = NSButton.buttonWithTitle_target_action_(title, self._target, action)
        btn.setFrame_(NSMakeRect(x, y, w, 26))
        btn.setTag_(tag)
        parent.addSubview_(btn)
        return btn

    def _table(self, parent, tag, columns, x, y, w, h, editable=True, on_select=None):
        """columns: list of (identifier, title, width)."""
        from AppKit import NSScrollView, NSTableColumn, NSTableView
        from Foundation import NSMakeRect

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(1)
        tv = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        for ident, title, cw in columns:
            col = NSTableColumn.alloc().initWithIdentifier_(ident)
            col.setTitle_(title)
            col.setWidth_(cw)
            col.setEditable_(editable)
            tv.addTableColumn_(col)
        src = _get_table_source_class().alloc().init()
        src.rows = []
        src.editable = editable
        src.on_select = on_select
        tv.setDataSource_(src)
        tv.setDelegate_(src)
        tv.setUsesAlternatingRowBackgroundColors_(True)
        scroll.setDocumentView_(tv)
        parent.addSubview_(scroll)
        self._tables[tag] = (tv, src)
        return tv, src

    # ---- tabs ---------------------------------------------------------------
    def _build_general(self):
        v = self._tab_view()
        y = 350
        self._label(v, "Dictation key:", 20, y)
        self._popup(v, "dictation_key", sorted(KEYS.keys()), 200, y - 2)
        y -= 34
        self._label(v, "Command key:", 20, y)
        self._popup(v, "command_key", ["none"] + sorted(KEYS.keys()), 200, y - 2)
        y -= 34
        self._label(v, "Language:", 20, y)
        self._combo(v, "language", LANG_CHOICES, 200, y - 2, 130)
        self._note(v, "auto = detect per dictation (mix English/Russian freely)", 200, y - 34)
        y -= 62
        self._label(v, "ASR model:", 20, y)
        self._combo(v, "asr_model", MODEL_CHOICES, 200, y - 2, 260)
        self._note(
            v,
            "large-v3-turbo-q4: multilingual, best speed/accuracy balance (~450MB). small.en: EN-only, lightest.",
            200,
            y - 34,
        )
        y -= 62
        self._label(v, "Insert text by:", 20, y)
        self._popup(v, "insertion_mode", ["paste", "type"], 200, y - 2, 130)
        y -= 34
        self._check(v, "keep_on_clipboard", "Keep transcript on clipboard after paste", 200, y)
        y -= 28
        self._check(v, "restore_clipboard", "Restore previous clipboard after paste", 200, y)
        return v

    def _build_overlay(self):
        v = self._tab_view()
        y = 350
        self._check(v, "overlay_enabled", "Show overlay during dictation", 200, y)
        y -= 30
        self._check(v, "always_visible", "Always-visible status pill", 200, y)
        y -= 34
        self._label(v, "Pill width:", 20, y)
        self._field(v, "overlay_width", 200, y - 2, 80)
        y -= 40
        self._add_button(v, "Reset Position to Bottom-Center", 200, y, 240, "resetPositionClicked:")
        self._note(
            v, "The pill can also be dragged anywhere with the mouse; its position is saved.", 200, y - 34
        )
        return v

    def _build_dictionary(self):
        v = self._tab_view()
        self._label(v, "Manual terms:", 20, 360)
        self._table(v, "dict_terms", [("term", "Term (biases recognition)", 300)], 200, 220, 340, 160)
        self._add_button(v, "+", 545, 350, 30, "addRow:", tag=1)
        self._add_button(v, "−", 545, 320, 30, "removeRow:", tag=1)
        self._label(v, "Learned (auto):", 20, 190)
        self._table(
            v,
            "dict_learned",
            [("src", "Heard / Term", 170), ("dst", "Correction", 160)],
            200,
            50,
            340,
            150,
            editable=False,
        )
        self._add_button(v, "Delete", 545, 170, 60, "removeRow:", tag=2)
        self._note(v, "Learned rows come from “Fix Last Transcription” — deletion is immediate.", 200, 12)
        return v

    def _build_snippets(self):
        v = self._tab_view()
        self._label(v, "Triggers:", 20, 360)
        self._table(
            v,
            "snippets",
            [("trigger", "Say this phrase…", 220)],
            120,
            170,
            250,
            210,
            on_select=self._snippet_selected,
        )
        self._add_button(v, "+", 375, 350, 30, "addRow:", tag=3)
        self._add_button(v, "−", 375, 320, 30, "removeRow:", tag=3)
        self._label(v, "…insert:", 420, 360, 80)
        from AppKit import NSFont, NSScrollView, NSTextView
        from Foundation import NSMakeRect

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(420, 170, 185, 180))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(1)
        body = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 185, 180))
        body.setFont_(NSFont.userFixedPitchFontOfSize_(11))
        body.setRichText_(False)
        scroll.setDocumentView_(body)
        v.addSubview_(scroll)
        self._w["snippet_body"] = body
        self._note(
            v,
            "Select a trigger to edit its text. The snippet is inserted verbatim when you "
            "dictate exactly the trigger phrase (Cyrillic works).",
            120,
            120,
            480,
        )
        return v

    def _build_styles(self):
        v = self._tab_view()
        y = 350
        self._label(v, "Default style:", 20, y)
        presets = list(self.controller.styles.presets().keys())
        self._popup(v, "default_style", presets, 200, y - 2, 160)
        y -= 44
        self._label(v, "Per-app styles:", 20, y)
        self._table(
            v,
            "per_app",
            [("bundle", "Bundle id (e.g. com.apple.mail)", 240), ("style", "Style", 110)],
            200,
            120,
            370,
            230,
        )
        self._add_button(v, "+", 575, 320, 30, "addRow:", tag=4)
        self._add_button(v, "−", 575, 290, 30, "removeRow:", tag=4)
        self._note(
            v,
            "Find a bundle id in Terminal:  osascript -e 'id of app \"Mail\"'. "
            "The pill's style chip overrides all of this until set back to Auto.",
            200,
            80,
        )
        return v

    def _build_advanced(self):
        v = self._tab_view()
        y = 350
        self._label(v, "LLM backend:", 20, y)
        self._popup(v, "llm_backend", BACKENDS, 200, y - 2, 130)
        self._note(v, "auto = Ollama if running, else built-in MLX, else rule-based cleanup", 200, y - 30)
        y -= 60
        self._label(v, "Ollama URL:", 20, y)
        self._field(v, "ollama_url", 200, y - 2, 260)
        y -= 32
        self._label(v, "Ollama model:", 20, y)
        self._field(v, "ollama_model", 200, y - 2, 260)
        y -= 32
        self._label(v, "MLX model:", 20, y)
        self._field(v, "mlx_model", 200, y - 2, 320)
        y -= 32
        self._label(v, "LLM idle unload (s):", 20, y)
        self._field(v, "idle_unload", 200, y - 2, 80)
        y -= 32
        self._check(v, "cleanup_enabled", "AI cleanup pass enabled", 200, y)
        y -= 34
        self._label(v, "Log level:", 20, y)
        self._popup(v, "log_level", LOG_LEVELS, 200, y - 2, 120)
        return v

    # ---- populate from config -------------------------------------------------
    def _populate(self):
        cfg = self.config
        g = self._w
        g["dictation_key"].selectItemWithTitle_(str(cfg.get("hotkeys.dictation_key", "fn")))
        g["command_key"].selectItemWithTitle_(str(cfg.get("hotkeys.command_key", "right_option")))
        g["language"].setStringValue_(str(cfg.get("asr.language", "auto")))
        g["asr_model"].setStringValue_(str(cfg.get("asr.model", "large-v3-turbo-q4")))
        g["insertion_mode"].selectItemWithTitle_(str(cfg.get("insertion.mode", "paste")))
        g["keep_on_clipboard"].setState_(1 if cfg.get("insertion.keep_on_clipboard", False) else 0)
        g["restore_clipboard"].setState_(1 if cfg.get("insertion.restore_clipboard", True) else 0)
        g["overlay_enabled"].setState_(1 if cfg.get("overlay.enabled", True) else 0)
        g["always_visible"].setState_(1 if cfg.get("overlay.always_visible", True) else 0)
        g["overlay_width"].setStringValue_(str(cfg.get("overlay.width", 400)))
        g["llm_backend"].selectItemWithTitle_(str(cfg.get("llm.backend", "auto")))
        g["ollama_url"].setStringValue_(str(cfg.get("llm.ollama.url", "http://localhost:11434")))
        g["ollama_model"].setStringValue_(str(cfg.get("llm.ollama.model", "qwen2.5:3b-instruct")))
        g["mlx_model"].setStringValue_(
            str(cfg.get("llm.mlx.model", "mlx-community/Qwen2.5-3B-Instruct-4bit"))
        )
        g["idle_unload"].setStringValue_(str(cfg.get("llm.mlx.idle_unload_s", 300)))
        g["cleanup_enabled"].setState_(1 if cfg.get("llm.cleanup_enabled", True) else 0)
        g["log_level"].selectItemWithTitle_(str(cfg.get("logging.level", "INFO")).upper())

        default_style = str(cfg.get("styles.default", "neutral"))
        pop = g["default_style"]
        pop.removeAllItems()
        pop.addItemsWithTitles_(list(self.controller.styles.presets().keys()))
        pop.selectItemWithTitle_(default_style)

        # tables
        tv, src = self._tables["dict_terms"]
        src.rows = [{"term": t} for t in (cfg.get("dictionary.entries", []) or [])]
        tv.reloadData()

        tv, src = self._tables["dict_learned"]
        d = self.controller.dictionary
        rows = [{"src": s, "dst": t, "_kind": "repl"} for s, t in d.learned_replacements()]
        rows += [{"src": t, "dst": "(term)", "_kind": "term"} for t in d.learned_terms()]
        src.rows = rows
        tv.reloadData()

        self._snippet_bodies = {
            str(k): str(v) for k, v in (cfg.get("snippets", {}) or {}).items()
        }
        tv, src = self._tables["snippets"]
        src.rows = [{"trigger": k} for k in self._snippet_bodies]
        tv.reloadData()
        self._current_snippet_row = -1
        self._w["snippet_body"].setString_("")

        tv, src = self._tables["per_app"]
        src.rows = [
            {"bundle": str(b), "style": str(s)}
            for b, s in (cfg.get("styles.per_app", {}) or {}).items()
        ]
        tv.reloadData()

    # ---- snippet body sync -----------------------------------------------------
    def _snippet_selected(self, row):
        self._store_snippet_body()
        tv, src = self._tables["snippets"]
        if 0 <= row < len(src.rows):
            trig = src.rows[row]["trigger"]
            self._w["snippet_body"].setString_(self._snippet_bodies.get(trig, ""))
            self._current_snippet_row = row
        else:
            self._current_snippet_row = -1
            self._w["snippet_body"].setString_("")

    def _store_snippet_body(self):
        row = self._current_snippet_row
        tv, src = self._tables["snippets"]
        if 0 <= row < len(src.rows):
            trig = src.rows[row]["trigger"]
            self._snippet_bodies[trig] = str(self._w["snippet_body"].string())

    # ---- table add/remove ---------------------------------------------------------
    def _table_add(self, tag):
        mapping = {1: "dict_terms", 3: "snippets", 4: "per_app"}
        name = mapping.get(tag)
        if not name:
            return
        tv, src = self._tables[name]
        if name == "dict_terms":
            src.rows.append({"term": "NewTerm"})
        elif name == "snippets":
            self._store_snippet_body()
            trig = "new trigger phrase"
            src.rows.append({"trigger": trig})
            self._snippet_bodies.setdefault(trig, "")
        elif name == "per_app":
            src.rows.append({"bundle": "com.example.app", "style": "formal"})
        tv.reloadData()
        tv.editColumn_row_withEvent_select_(0, len(src.rows) - 1, None, True)

    def _table_remove(self, tag):
        mapping = {1: "dict_terms", 2: "dict_learned", 3: "snippets", 4: "per_app"}
        name = mapping.get(tag)
        if not name:
            return
        tv, src = self._tables[name]
        row = tv.selectedRow()
        if row < 0 or row >= len(src.rows):
            return
        if name == "dict_learned":
            entry = src.rows[row]
            d = self.controller.dictionary
            if entry.get("_kind") == "repl":
                d.remove_learned_replacement(entry["src"])
            else:
                d.remove_learned_term(entry["src"])
        elif name == "snippets":
            self._snippet_bodies.pop(src.rows[row]["trigger"], None)
            self._current_snippet_row = -1
            self._w["snippet_body"].setString_("")
        del src.rows[row]
        tv.reloadData()

    # ---- actions --------------------------------------------------------------
    def _edit_file(self):
        subprocess.Popen(["open", self.config.path])

    def _reset_position(self):
        write_config_values(self.config.path, {"overlay.position": None})
        self.controller.overlay.refresh_position()

    def _alert(self, title, message):
        from AppKit import NSAlert

        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.runModal()

    def _apply(self):
        try:
            errors, updates = self._collect()
        except Exception as e:
            log.exception("settings collect failed")
            self._alert("Settings error", str(e))
            return
        if errors:
            self._alert("Please fix these settings", "\n".join(f"• {e}" for e in errors))
            return
        # only write keys that actually changed (preserves inline comments
        # on untouched lines); compare with trailing newlines normalized so
        # YAML block scalars ("body\n") don't register as phantom changes
        def _norm(v):
            if isinstance(v, str):
                return v.rstrip("\n")
            if isinstance(v, dict):
                return {k: _norm(x) for k, x in v.items()}
            return v

        changed = {
            k: v for k, v in updates.items() if _norm(self.config.get(k)) != _norm(v)
        }
        if not changed:
            self._alert("Murmur", "No changes to apply.")
            return
        if write_config_values(self.config.path, changed):
            log.info("settings applied: %s", sorted(changed))
            self._alert(
                "Settings saved",
                "Changes were written to config.yaml and take effect within a second.",
            )
        else:
            self._alert(
                "Could not write config",
                "See ~/.murmur/murmur.log for details. Your file was not changed.",
            )

    # ---- gather + validate ---------------------------------------------------
    def _collect(self):
        g = self._w
        errors = []
        updates = {}

        dictation = str(g["dictation_key"].titleOfSelectedItem())
        command = str(g["command_key"].titleOfSelectedItem())
        if dictation not in KEYS:
            errors.append(f"Unknown dictation key: {dictation}")
        if command != "none" and command not in KEYS:
            errors.append(f"Unknown command key: {command}")
        if command == dictation:
            errors.append("Dictation and Command keys must be different.")
        updates["hotkeys.dictation_key"] = dictation
        updates["hotkeys.command_key"] = command

        lang = str(g["language"].stringValue()).strip() or "auto"
        if not (lang == "auto" or (2 <= len(lang) <= 3 and lang.isalpha())):
            errors.append(f"Language must be 'auto' or an ISO code (got '{lang}').")
        updates["asr.language"] = lang.lower()

        model = str(g["asr_model"].stringValue()).strip()
        if not model:
            errors.append("ASR model must not be empty.")
        updates["asr.model"] = model

        updates["insertion.mode"] = str(g["insertion_mode"].titleOfSelectedItem())
        updates["insertion.keep_on_clipboard"] = bool(g["keep_on_clipboard"].state())
        updates["insertion.restore_clipboard"] = bool(g["restore_clipboard"].state())

        updates["overlay.enabled"] = bool(g["overlay_enabled"].state())
        updates["overlay.always_visible"] = bool(g["always_visible"].state())
        try:
            width = int(float(str(g["overlay_width"].stringValue())))
            if not 200 <= width <= 1200:
                raise ValueError
            updates["overlay.width"] = width
        except ValueError:
            errors.append("Pill width must be a number between 200 and 1200.")

        # dictionary manual terms
        _, src = self._tables["dict_terms"]
        terms = [r["term"].strip() for r in src.rows if r.get("term", "").strip()]
        if len(set(t.lower() for t in terms)) != len(terms):
            errors.append("Dictionary terms contain duplicates.")
        updates["dictionary.entries"] = terms

        # snippets
        self._store_snippet_body()
        _, ssrc = self._tables["snippets"]
        snippets = {}
        for r in ssrc.rows:
            trig = r.get("trigger", "").strip()
            if not trig:
                errors.append("A snippet has an empty trigger phrase.")
                continue
            body = self._snippet_bodies.get(r["trigger"], "").strip("\n")
            if not body.strip():
                errors.append(f"Snippet '{trig}' has empty text.")
                continue
            if trig.lower() in (k.lower() for k in snippets):
                errors.append(f"Duplicate snippet trigger: '{trig}'")
                continue
            snippets[trig] = body
        updates["snippets"] = snippets

        # styles
        presets = self.controller.styles.presets()
        default_style = str(g["default_style"].titleOfSelectedItem() or "neutral")
        if default_style not in presets:
            errors.append(f"Unknown default style: {default_style}")
        updates["styles.default"] = default_style
        _, psrc = self._tables["per_app"]
        per_app = {}
        for r in psrc.rows:
            bundle = r.get("bundle", "").strip()
            style = r.get("style", "").strip()
            if not bundle or "." not in bundle:
                errors.append(f"Per-app row has an invalid bundle id: '{bundle}'")
                continue
            if style not in presets:
                errors.append(
                    f"Per-app style '{style}' is not a known preset "
                    f"({', '.join(presets)})."
                )
                continue
            per_app[bundle] = style
        updates["styles.per_app"] = per_app

        # advanced
        updates["llm.backend"] = str(g["llm_backend"].titleOfSelectedItem())
        url = str(g["ollama_url"].stringValue()).strip()
        if not url.startswith(("http://localhost", "http://127.0.0.1")):
            errors.append("Ollama URL must be a localhost address (Murmur never talks to the network).")
        updates["llm.ollama.url"] = url
        updates["llm.ollama.model"] = str(g["ollama_model"].stringValue()).strip()
        updates["llm.mlx.model"] = str(g["mlx_model"].stringValue()).strip()
        try:
            idle = int(float(str(g["idle_unload"].stringValue())))
            if idle < 0:
                raise ValueError
            updates["llm.mlx.idle_unload_s"] = idle
        except ValueError:
            errors.append("LLM idle unload must be a non-negative number of seconds.")
        updates["llm.cleanup_enabled"] = bool(g["cleanup_enabled"].state())
        updates["logging.level"] = str(g["log_level"].titleOfSelectedItem())

        return errors, updates
