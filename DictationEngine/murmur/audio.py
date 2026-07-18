"""Microphone capture via sounddevice.

A small always-on input stream feeds a rolling ring buffer (~500ms) so the
first word isn't clipped when the hotkey fires. While recording, callback
blocks are appended to a growing list; `stop_recording()` returns the whole
utterance (pre-roll + recording) as one float32 mono array at 16kHz.
"""

import collections
import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)


class AudioCapture:
    def __init__(self, config):
        self.config = config
        self._stream = None
        self._lock = threading.Lock()
        self._ring: collections.deque = collections.deque()
        self._ring_samples = 0
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._level = 0.0
        self._level_history: collections.deque = collections.deque(maxlen=48)
        self._error: Optional[str] = None
        self.on_error: Optional[Callable[[str], None]] = None

    # -- properties ------------------------------------------------------
    @property
    def sample_rate(self) -> int:
        return int(self.config.get("audio.sample_rate", 16000))

    @property
    def level(self) -> float:
        return self._level

    def level_history(self) -> list[float]:
        with self._lock:
            return list(self._level_history)

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    # -- stream lifecycle --------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("audio status: %s", status)
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        rms = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
        # quick attack, slow decay, roughly perceptual scaling
        target = min(1.0, rms * 12.0)
        self._level = max(target, self._level * 0.82)
        with self._lock:
            self._level_history.append(self._level)
            if self._recording:
                self._chunks.append(mono)
            else:
                self._ring.append(mono)
                self._ring_samples += len(mono)
                max_samples = int(
                    self.sample_rate * self.config.get("audio.preroll_ms", 500) / 1000.0
                )
                while self._ring_samples > max_samples and len(self._ring) > 1:
                    dropped = self._ring.popleft()
                    self._ring_samples -= len(dropped)

    def ensure_stream(self) -> bool:
        """Open the input stream if needed. Returns False on failure
        (typically: microphone permission denied or no input device)."""
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd

            device = self.config.get("audio.input_device")
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=int(self.sample_rate * 0.03),  # 30ms blocks
                device=device,
                callback=self._callback,
            )
            self._stream.start()
            self._error = None
            log.info("audio stream open (device=%s, %dHz)", device or "default", self.sample_rate)
            return True
        except Exception as e:
            self._stream = None
            self._error = f"microphone unavailable: {e}"
            log.error(self._error)
            if self.on_error:
                self.on_error(self._error)
            return False

    def close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def start_if_always_on(self) -> None:
        if self.config.get("audio.always_on_capture", True):
            self.ensure_stream()

    # -- recording ---------------------------------------------------------
    def start_recording(self) -> bool:
        if not self.ensure_stream():
            return False
        with self._lock:
            if self._recording:
                return True
            # seed with pre-roll from the ring buffer
            self._chunks = list(self._ring)
            self._recording = True
        log.debug("recording started (preroll %d chunks)", len(self._chunks))
        return True

    def stop_recording(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
            self._ring.clear()
            self._ring_samples = 0
        if not self.config.get("audio.always_on_capture", True):
            self.close_stream()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)

    def abort_recording(self) -> None:
        with self._lock:
            self._recording = False
            self._chunks = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    def snapshot(self, last_seconds: Optional[float] = None) -> np.ndarray:
        """Copy of the audio recorded so far (optionally only the tail)."""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            data = np.concatenate(self._chunks).astype(np.float32)
        if last_seconds:
            n = int(last_seconds * self.sample_rate)
            data = data[-n:]
        return data

    def recorded_seconds(self) -> float:
        with self._lock:
            n = sum(len(c) for c in self._chunks)
        return n / float(self.sample_rate)
