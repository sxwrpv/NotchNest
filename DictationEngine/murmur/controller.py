"""Orchestration: hotkey events -> record -> transcribe -> snippet/cleanup ->
dictionary safety net -> inject. Also Command Mode and the partial-transcript
loop. All heavy work runs on a single worker thread so jobs serialize."""

import logging
import queue
import threading
import time
from enum import Enum
from typing import Callable, Optional

from .audio import AudioCapture
from .cleanup import CleanupEngine
from .dictionary import PersonalDictionary
from .inject import TextInjector, frontmost_app_info
from .llm import LLMRouter, LLMUnavailable
from .overlay import Overlay
from .snippets import Snippets
from .styles import StyleManager
from .transcriber import MLXWhisperTranscriber, is_degenerate

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    COMMAND = "command"
    PROCESSING = "processing"


class Controller:
    def __init__(self, config, db_path: str):
        self.config = config
        self.audio = AudioCapture(config)
        self.router = LLMRouter(config)
        self.cleanup = CleanupEngine(config, self.router)
        self.dictionary = PersonalDictionary(config, db_path)
        self.snippets = Snippets(config)
        self.styles = StyleManager(config)
        self.injector = TextInjector(config)
        self.overlay = Overlay(config, level_provider=self.audio.level_history)

        self.state = State.IDLE
        self.enabled = True
        self._state_lock = threading.Lock()
        self._asr_model_name: Optional[str] = None
        self._transcriber: Optional[MLXWhisperTranscriber] = None
        self._session_bundle: Optional[str] = None
        self._session_pid: Optional[int] = None
        self._session_kind = "dictation"  # or "command"
        self._command_selection: Optional[str] = None
        self._partial_stop = threading.Event()
        self._partial_thread: Optional[threading.Thread] = None
        # Generation counter: cancel() bumps it so an in-flight _process job
        # discovers it's been orphaned and discards its result.
        self._gen = 0
        # Style override from the pill's switcher; None = Auto (per-app/default)
        self.style_override: Optional[str] = None
        # one-shot flag: tell the user (once) when cleanup silently degraded
        self._llm_fallback_notified = False

        self.last_raw: str = ""
        self.last_final: str = ""
        # Surfaced to NotchNest through the notch bridge; None when healthy.
        self.last_error: Optional[str] = None

        # observers (menu bar wires in here)
        self.on_state_change: Optional[Callable[[State], None]] = None
        self.notify: Callable[[str, str], None] = lambda title, msg: log.info(
            "notify: %s — %s", title, msg
        )

        self._jobs: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._drain, name="murmur-worker", daemon=True)
        self._worker.start()

        self.audio.on_error = lambda msg: self.notify("Murmur — microphone", msg)

        # interactive status pill wiring ("quit" is added by the menu bar)
        self.overlay.callbacks.update(
            {
                "toggle": self.toggle_dictation,
                "cancel": self.cancel,
                "cycle_style": self.cycle_style,
                "style_label": self.current_style_label,
                "open_settings": self._open_settings,
                "hide": lambda: self.overlay.set_always_visible(False),
                "copy_last": self._pill_copy_last,
                "has_last": lambda: bool(self.last_final),
            }
        )

    # ---- plumbing ---------------------------------------------------------
    def transcriber(self) -> MLXWhisperTranscriber:
        name = str(self.config.get("asr.model", "small.en"))
        if self._transcriber is None or self._asr_model_name != name:
            self._transcriber = MLXWhisperTranscriber(name)
            self._asr_model_name = name
        return self._transcriber

    def _set_state(self, s: State):
        with self._state_lock:
            self.state = s
        if self.on_state_change:
            try:
                self.on_state_change(s)
            except Exception:
                log.exception("state observer failed")

    def _drain(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                log.exception("worker job failed")
            finally:
                self._jobs.task_done()

    def submit(self, fn: Callable[[], None]):
        self._jobs.put(fn)

    def startup(self):
        """Non-blocking warm start. The pill shows immediately; the mic open
        and ASR warmup run on the worker thread because the very first mic
        open can block on the macOS permission dialog — that must never
        stall the main thread/UI."""
        self.overlay.start()

        def _warm():
            self.audio.start_if_always_on()
            self.transcriber().warmup()

        self.submit(_warm)

    # ---- hotkey entry points (each returns fast) ---------------------------
    def toggle_dictation(self):
        if not self.enabled:
            return
        if self.state == State.IDLE:
            self._begin(kind="dictation")
        elif self.state in (State.LISTENING, State.COMMAND):
            self._finish()

    def ptt_start(self):
        if not self.enabled or self.state != State.IDLE:
            return
        self._begin(kind="dictation")

    def ptt_stop(self):
        if self.state == State.LISTENING and self._session_kind == "dictation":
            self._finish()

    def command_start(self):
        if not self.enabled or self.state != State.IDLE:
            return
        self._begin(kind="command")

    def command_stop(self):
        if self.state == State.COMMAND:
            self._finish()

    def cancel(self):
        """Abort the current session. During LISTENING/COMMAND this discards
        the recording; during PROCESSING it orphans the in-flight job so its
        result is discarded instead of pasted (the decode/LLM call itself
        can't be interrupted mid-flight, but its output goes nowhere)."""
        if self.state in (State.LISTENING, State.COMMAND):
            self._gen += 1
            self._stop_partials()
            self.audio.abort_recording()
            self.overlay.end_session()
            self._set_state(State.IDLE)
            log.info("session canceled while recording")
        elif self.state == State.PROCESSING:
            self._gen += 1
            self.overlay.end_session()
            self._set_state(State.IDLE)
            log.info("in-flight transcription canceled; result will be discarded")

    # ---- session lifecycle --------------------------------------------------
    def _begin(self, kind: str):
        self._gen += 1
        self._session_bundle, self._session_pid = frontmost_app_info()
        self._session_kind = kind
        self._command_selection = None

        if kind == "command":
            # capture the selection before any audio UX, while it's fresh
            sel = self.injector.copy_selection()
            if not sel or not sel.strip():
                self.notify("Murmur", "Command Mode: no text selected in the frontmost app.")
                self.overlay.flash_error("No selection — select text, then hold the command key")
                return
            self._command_selection = sel

        if not self.audio.start_recording():
            self.overlay.flash_error("Microphone unavailable — check System Settings › Privacy")
            return
        self._set_state(State.COMMAND if kind == "command" else State.LISTENING)
        self.overlay.show("command" if kind == "command" else "listening")
        if kind == "dictation" and self.config.get("asr.partials", True):
            self._start_partials()
        log.info("%s started (app=%s)", kind, self._session_bundle)

    def _finish(self):
        self._stop_partials()
        samples = self.audio.stop_recording()
        kind, bundle = self._session_kind, self._session_bundle
        pid = self._session_pid
        selection = self._command_selection
        dur = len(samples) / float(self.audio.sample_rate)
        if dur < float(self.config.get("audio.min_utterance_s", 0.35)):
            log.info("utterance too short (%.2fs), discarded", dur)
            self.overlay.hide()
            self._set_state(State.IDLE)
            return
        # Digital silence is a broken input, not a quiet room. Transcribing it
        # makes Whisper hallucinate a repeated word, which then gets typed into
        # whatever app is focused — so refuse, say why, and reopen the stream so
        # a permission granted afterwards takes effect without a restart.
        peak, rms = self.audio.measure(samples)
        log.info("captured %.1fs: peak=%.4f rms=%.4f", dur, peak, rms)
        if self.audio.is_dead_signal(samples):
            dead_device = self.audio.is_silent_device(samples)
            log.error(
                "no speech in the buffer (peak=%.4f over %.1fs) — %s; "
                "discarding instead of transcribing", peak, dur,
                "input is digital silence" if dead_device else "nothing above the noise floor"
            )
            self.audio.recover()
            self.last_error = (
                "No audio reaching the microphone. Check System Settings › "
                "Privacy & Security › Microphone › NotchNest, and that the "
                "right input device is selected."
                if dead_device else
                "Heard only silence — check the microphone input level and "
                "that the right input device is selected."
            )
            self.overlay.flash_error("No audio from microphone")
            self._set_state(State.IDLE)
            return

        self._set_state(State.PROCESSING)
        self.overlay.set_mode("processing", "Transcribing…")
        gen = self._gen
        self.submit(lambda: self._process(samples, kind, bundle, pid, selection, gen))

    def _canceled(self, gen: int) -> bool:
        return gen != self._gen

    # ---- background processing ----------------------------------------------
    def _process(self, samples, kind, bundle, pid, selection, gen):
        try:
            t0 = time.time()
            raw = self.transcriber().transcribe(
                samples,
                initial_prompt=self.dictionary.initial_prompt(),
                language=self.config.get("asr.language", "en"),
            )
            log.info("ASR (%.1fs audio) in %.2fs: %r", len(samples) / 16000.0, time.time() - t0, raw)
            if self._canceled(gen):
                log.info("canceled: ASR result discarded")
                return
            self.last_raw = raw
            if not raw.strip():
                self.last_error = None
                self.overlay.flash_error("Heard nothing")
                return

            # Whisper answers audio it cannot resolve with one word repeated
            # for the whole window. That is never dictation, and it used to be
            # cleaned up and typed straight into whatever app had focus.
            if is_degenerate(raw):
                log.warning("discarding degenerate transcript: %r", raw[:80])
                self.last_error = (
                    "Couldn't make out any speech — nothing was inserted. "
                    "Check the microphone input level and that the right "
                    "input device is selected."
                )
                self.overlay.flash_error("Couldn't make out any speech")
                return
            self.last_error = None  # a real transcript got through

            if kind == "command":
                self._process_command(raw, selection, bundle, pid, gen)
                return

            # snippets run on the raw utterance, before any rewriting
            snippet = self.snippets.match(raw)
            if snippet is not None:
                final = snippet
                engine = "snippet"
            else:
                style_name, style_text = self.styles.resolve(
                    bundle, override=self.style_override
                )
                self.overlay.set_mode("processing", "Cleaning up…")
                final, engine = self.cleanup.clean(
                    raw,
                    style_instructions=style_text,
                    dictionary_terms=self.dictionary.all_terms(),
                )
                final = self.dictionary.apply_replacements(final)
                log.info("cleanup via %s (style=%s): %r", engine, style_name, final)
                # visible (one-time) note when we degraded to rule-based
                # cleanup even though an LLM backend is configured
                if (
                    engine == "rules"
                    and self.config.get("llm.cleanup_enabled", True)
                    and str(self.config.get("llm.backend", "auto")) != "none"
                    and not self._llm_fallback_notified
                ):
                    self._llm_fallback_notified = True
                    self.notify(
                        "Murmur — basic cleanup",
                        "The local LLM isn't available (not running or still "
                        "downloading), so Murmur used rule-based cleanup. "
                        "Text was still inserted.",
                    )

            if self._canceled(gen):
                log.info("canceled: cleaned result discarded (was %r)", final[:80])
                return
            self.last_final = final
            self._deliver(final, bundle, pid)
        finally:
            if not self._canceled(gen):
                # flash_error() (e.g. "Heard nothing") schedules its own
                # delayed revert-to-idle; don't stomp on it here or the
                # message would be replaced before the user ever sees it.
                if self.overlay.state()[0] != "error":
                    self.overlay.end_session()
                self._set_state(State.IDLE)
            # if canceled, cancel() already reset the overlay and state

    def _deliver(self, text: str, bundle: Optional[str], pid: Optional[int]) -> None:
        """Insert `text` into the app the session started in. If that app is
        no longer available (user switched away and re-activation failed, or
        dictation started over Murmur's own UI), never paste into the wrong
        app — put the text on the clipboard and say so."""
        status = self.injector.insert(text, target_bundle=bundle, target_pid=pid)
        if status == "inserted":
            return
        if status == "wrong_app":
            self.injector.copy_to_clipboard(text)
            log.warning(
                "did not paste (target %s unavailable); %d chars copied to clipboard",
                bundle,
                len(text),
            )
            self.notify(
                "Murmur",
                "App changed during transcription — text copied to clipboard instead.",
            )
            self.overlay.flash_error("Copied to clipboard (app changed)")
            return
        self.notify(
            "Murmur", "Could not insert text — is Accessibility permission granted?"
        )

    def _process_command(self, instruction: str, selection: Optional[str],
                         bundle: Optional[str], pid: Optional[int], gen: int):
        if not selection:
            self.overlay.flash_error("No selection captured")
            return
        self.overlay.set_mode("processing", f"“{instruction.strip()}”…")
        try:
            rewritten, engine = self.cleanup.rewrite(selection, instruction)
        except LLMUnavailable as e:
            log.warning("command mode failed: %s", e)
            self.notify(
                "Murmur — Command Mode",
                "No local LLM available. Start Ollama or set llm.backend to mlx.",
            )
            self.overlay.flash_error("Command Mode needs the local LLM (Ollama/MLX)")
            return
        if self._canceled(gen):
            log.info("canceled: command rewrite discarded")
            return
        self.last_final = rewritten
        log.info("command rewrite via %s: %r -> %r", engine, instruction, rewritten[:120])
        self._deliver(rewritten, bundle, pid)

    # ---- streaming partials ---------------------------------------------------
    def _start_partials(self):
        self._partial_stop.clear()

        def _loop():
            interval = float(self.config.get("asr.partial_interval_ms", 1500)) / 1000.0
            window = float(self.config.get("asr.partial_window_s", 12))
            tr = self.transcriber()
            while not self._partial_stop.wait(interval):
                if self.state != State.LISTENING:
                    break
                if self.audio.recorded_seconds() < 0.8:
                    continue
                audio = self.audio.snapshot(last_seconds=window)
                text = tr.try_transcribe(
                    audio, initial_prompt=self.dictionary.initial_prompt()
                )
                if text:
                    self.overlay.set_text(text)

        self._partial_thread = threading.Thread(target=_loop, name="partials", daemon=True)
        self._partial_thread.start()

    def _stop_partials(self):
        self._partial_stop.set()

    # ---- corrections / dictionary (menu bar actions) ----------------------------
    def record_correction(self, corrected: str) -> list:
        learned = self.dictionary.record_correction(self.last_final, corrected)
        self.last_final = corrected
        return learned

    def status_line(self) -> str:
        llm = self.router.status()
        return f"ASR: {self.config.get('asr.model')} · LLM: {llm}"

    # ---- status pill (menu bar toggle) --------------------------------------
    def toggle_overlay_visible(self) -> bool:
        """Menu bar 'Show Status Pill' checkbox. Returns the new state."""
        new_val = not self.overlay.is_always_visible()
        self.overlay.set_always_visible(new_val)
        return new_val

    # ---- style override (pill's hover chip) -----------------------------------
    def cycle_style(self) -> str:
        """Advance the style override: Auto -> neutral -> formal -> casual ->
        (custom presets) -> Auto. Applies to subsequent dictations until
        changed. Returns the new display label."""
        self.style_override = self.styles.next_in_cycle(self.style_override)
        label = self.current_style_label()
        log.info("style override -> %s", label)
        return label

    def current_style_label(self) -> str:
        if self.style_override is None:
            return "Auto"
        return self.style_override.capitalize()

    def _open_settings(self):
        # Prefer the native Settings window (wired in by the menu bar);
        # fall back to opening the YAML in the default editor.
        gui = getattr(self, "open_settings_gui", None)
        if gui is not None:
            gui()
            return
        import subprocess

        subprocess.Popen(["open", self.config.path])

    # ---- copy last transcription ----------------------------------------------
    def copy_last_transcription(self) -> bool:
        """Put the most recent final transcription on the clipboard (no
        restore — it stays there). Returns False if nothing exists yet."""
        text = self.last_final
        if not text:
            return False
        ok = self.injector.copy_to_clipboard(text)
        if ok:
            log.info("copied last transcription to clipboard (%d chars)", len(text))
        return ok

    def _pill_copy_last(self):
        if not self.copy_last_transcription():
            self.overlay.flash_error("Nothing to copy yet", seconds=1.5)

    # ---- config hot-reload --------------------------------------------------
    def on_config_reload(self):
        # transcriber picks up model changes lazily; audio stream may need a restart
        if self.config.get("audio.always_on_capture", True):
            self.audio.ensure_stream()
        # pill position may have been edited by hand (or by a finished drag)
        self.overlay.refresh_position()
        log.info("controller applied reloaded config")
