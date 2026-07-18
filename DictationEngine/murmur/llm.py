"""Local LLM access. Zero non-localhost network I/O.

Backends:
  - OllamaBackend: talks to a local Ollama server over http://localhost:11434.
    keep_alive is set short so the model unloads from RAM when idle.
  - MLXBackend:    runs a quantized model in-process via mlx-lm, with an idle
    timer that unloads weights — same RAM philosophy, no extra install needed.
  - "auto" routing prefers Ollama when it's running, else MLX, else nothing
    (callers fall back to rule-based cleanup).
"""

import gc
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


class LLMBackend(ABC):
    name = "abstract"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def generate(self, system: str, user: str, max_tokens: int = 1024,
                 temperature: float = 0.15) -> str: ...

    def unload(self) -> None:
        pass


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(self, config):
        self.config = config
        self._last_check = 0.0
        self._last_ok = False

    def _url(self) -> str:
        return str(self.config.get("llm.ollama.url", "http://localhost:11434")).rstrip("/")

    def available(self) -> bool:
        # Cache the health check for a few seconds; it's called on every hotkey.
        now = time.time()
        if now - self._last_check < 5.0:
            return self._last_ok
        self._last_check = now
        try:
            import requests

            r = requests.get(self._url() + "/api/tags", timeout=0.8)
            self._last_ok = r.status_code == 200
        except Exception:
            self._last_ok = False
        return self._last_ok

    def generate(self, system, user, max_tokens=1024, temperature=0.15) -> str:
        import requests

        payload = {
            "model": self.config.get("llm.ollama.model", "qwen2.5:3b-instruct"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.config.get("llm.ollama.keep_alive", "5m"),
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            r = requests.post(self._url() + "/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            data = r.json()
            return (data.get("message") or {}).get("content", "").strip()
        except Exception as e:
            self._last_ok = False
            raise LLMUnavailable(f"ollama request failed: {e}") from e


class MLXBackend(LLMBackend):
    """In-process mlx-lm backend with idle auto-unload (RAM headroom on 16GB)."""

    name = "mlx"

    def __init__(self, config):
        self.config = config
        # RLock: generate() holds the lock while calling _ensure_loaded(),
        # which can call unload() (e.g. to swap models) — that nested call
        # must be able to re-acquire on the same thread, or it deadlocks.
        self._lock = threading.RLock()
        self._model = None
        self._tokenizer = None
        self._model_id: Optional[str] = None
        self._unload_timer: Optional[threading.Timer] = None
        self._importable: Optional[bool] = None

    def available(self) -> bool:
        if self._importable is None:
            try:
                import mlx_lm  # noqa: F401

                self._importable = True
            except Exception:
                self._importable = False
        return bool(self._importable)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self):
        from mlx_lm import load

        model_id = self.config.get("llm.mlx.model", "mlx-community/Qwen2.5-3B-Instruct-4bit")
        if self._model is None or self._model_id != model_id:
            self.unload()
            log.info("loading MLX LLM %s ...", model_id)
            t0 = time.time()
            self._model, self._tokenizer = load(model_id)
            self._model_id = model_id
            log.info("MLX LLM loaded in %.1fs", time.time() - t0)
        return self._model, self._tokenizer

    def _schedule_unload(self):
        idle = float(self.config.get("llm.mlx.idle_unload_s", 300))
        if self._unload_timer:
            self._unload_timer.cancel()
        if idle <= 0:
            return
        self._unload_timer = threading.Timer(idle, self.unload)
        self._unload_timer.daemon = True
        self._unload_timer.start()

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            log.info("unloading idle MLX LLM to free RAM")
            self._model = None
            self._tokenizer = None
            self._model_id = None
            gc.collect()
            try:
                import mlx.core as mx

                if hasattr(mx, "clear_cache"):
                    mx.clear_cache()
                elif hasattr(mx, "metal"):
                    mx.metal.clear_cache()
            except Exception:
                pass

    def generate(self, system, user, max_tokens=1024, temperature=0.15) -> str:
        if not self.available():
            raise LLMUnavailable("mlx-lm not importable")
        with self._lock:
            try:
                from mlx_lm import generate

                model, tokenizer = self._ensure_loaded()
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                prompt = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                kwargs = {"max_tokens": max_tokens, "verbose": False}
                try:
                    from mlx_lm.sample_utils import make_sampler

                    kwargs["sampler"] = make_sampler(temp=temperature)
                except Exception:
                    pass  # older mlx-lm: greedy default is fine
                text = generate(model, tokenizer, prompt=prompt, **kwargs)
                return (text or "").strip()
            except LLMUnavailable:
                raise
            except Exception as e:
                raise LLMUnavailable(f"mlx generation failed: {e}") from e
            finally:
                self._schedule_unload()


class LLMRouter:
    """Picks a backend per request according to llm.backend config."""

    def __init__(self, config):
        self.config = config
        self.ollama = OllamaBackend(config)
        self.mlx = MLXBackend(config)

    def pick(self) -> Optional[LLMBackend]:
        mode = str(self.config.get("llm.backend", "auto")).lower()
        if mode == "none":
            return None
        if mode == "ollama":
            return self.ollama if self.ollama.available() else None
        if mode == "mlx":
            return self.mlx if self.mlx.available() else None
        # auto
        if self.ollama.available():
            return self.ollama
        if self.mlx.available():
            return self.mlx
        return None

    def status(self) -> str:
        b = self.pick()
        return b.name if b else "none (rule-based)"

    def generate(self, system: str, user: str) -> tuple[str, str]:
        """Returns (text, backend_name). Raises LLMUnavailable if no backend."""
        backend = self.pick()
        if backend is None:
            raise LLMUnavailable("no local LLM backend available")
        text = backend.generate(
            system,
            user,
            max_tokens=int(self.config.get("llm.max_tokens", 1024)),
            temperature=float(self.config.get("llm.temperature", 0.15)),
        )
        return text, backend.name
