"""ASR abstraction. The default implementation wraps mlx-whisper
(Metal-accelerated on Apple Silicon). Audio is always passed as a 16kHz
float32 numpy array, so no ffmpeg dependency is needed."""

import logging
import queue
import threading
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Short config names -> mlx-community repos (verified to exist).
MODEL_ALIASES = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo-q4": "mlx-community/whisper-large-v3-turbo-q4",
}


def resolve_model(name: str) -> str:
    if "/" in (name or ""):
        return name
    return MODEL_ALIASES.get(name, MODEL_ALIASES["large-v3-turbo-q4"])


class Transcriber(ABC):
    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        initial_prompt: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str: ...

    def warmup(self) -> None:  # optional
        pass


class MLXWhisperTranscriber(Transcriber):
    """mlx-whisper backed transcriber. Every decode runs on one dedicated
    thread (see _Decoder); a lock on top lets the partial loop skip rather
    than queue behind an in-flight decode."""

    def __init__(self, model: str = "large-v3-turbo-q4"):
        self.model_repo = resolve_model(model)
        self._lock = threading.Lock()
        self._decoder = _Decoder()
        self._loaded = False
        self.last_language: Optional[str] = None  # detected language of last decode

    def transcribe(self, audio, initial_prompt=None, language=None) -> str:
        if audio is None or len(audio) < 1600:  # <0.1s: nothing to do
            return ""
        import mlx_whisper

        kwargs = {}
        # language="auto" (or None) lets Whisper detect the language per
        # utterance, so English and Russian can be mixed freely. English-only
        # (.en) models never take a language argument.
        if language and language != "auto" and ".en" not in self.model_repo:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        with self._lock:
            result = self._decoder.run(
                lambda: mlx_whisper.transcribe(
                    audio.astype(np.float32),
                    path_or_hf_repo=self.model_repo,
                    condition_on_previous_text=False,
                    verbose=None,
                    **kwargs,
                )
            )
            self._loaded = True
            self.last_language = result.get("language")
        # Drop segments whisper itself thinks are silence/hallucination.
        segments = result.get("segments") or []
        kept = [
            s["text"]
            for s in segments
            if not (s.get("no_speech_prob", 0) > 0.66 and s.get("avg_logprob", 0) < -1.0)
        ]
        text = "".join(kept).strip() if segments else (result.get("text") or "").strip()
        return text

    def try_transcribe(self, audio, **kw) -> Optional[str]:
        """Non-blocking variant for the partial loop: skips if a decode is
        already in flight instead of queueing behind it."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            self._lock.release()  # transcribe() re-acquires
            return self.transcribe(audio, **kw)
        except Exception as e:
            log.debug("partial transcribe failed: %s", e)
            return None

    def warmup(self) -> None:
        """Load model weights (and download on first ever run) so the first
        real dictation doesn't pay the cold-start cost."""
        try:
            silence = np.zeros(int(16000 * 0.5), dtype=np.float32)
            self.transcribe(silence)
            log.info("ASR model warm: %s", self.model_repo)
        except Exception:
            log.exception("ASR warmup failed")


def is_degenerate(text: str) -> bool:
    """True when a transcript is one word repeated instead of speech.

    Whisper answers audio it cannot resolve with a single token repeated for
    the whole window. That output is never real dictation, and it used to be
    cleaned up, copied to the clipboard and typed into the focused app.
    """
    words = [w.strip(".,!?;:\u2026").lower() for w in text.split()]
    words = [w for w in words if w]
    # Three is the floor: the failure that started this ("Glossary Glossary
    # Glossary") was exactly three words. Losing a real "no no no" is a far
    # cheaper mistake than typing a wall of repeats into the user's document.
    if len(words) < 3:
        return False
    if len(set(words)) / len(words) <= 0.34:
        return True
    run = best = 1
    for prev, cur in zip(words, words[1:]):
        run = run + 1 if cur == prev else 1
        best = max(best, run)
    return best >= 6


class _Decoder:
    """Runs every mlx-whisper decode on one dedicated thread.

    mlx-whisper is not safe to drive from several threads even when the calls
    are serialized with a lock: the partial-transcript loop and the final
    decode ran on different threads, the shared model state got corrupted, and
    the final decode degenerated into one token repeated for the whole
    utterance ("Glossary Glossary Glossary…"). Pinning all model work to a
    single thread keeps live partials without poisoning the transcript.
    """

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="asr-decode", daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            fn, box, done = self._jobs.get()
            try:
                box.append(("ok", fn()))
            except BaseException as e:  # noqa: BLE001 - reraised on the caller
                box.append(("err", e))
            finally:
                done.set()

    def run(self, fn):
        box: list = []
        done = threading.Event()
        self._jobs.put((fn, box, done))
        done.wait()
        kind, value = box[0]
        if kind == "err":
            raise value
        return value
