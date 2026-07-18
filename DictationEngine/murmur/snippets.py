"""Voice-triggered snippets: if the whole utterance matches a trigger phrase
(from config.yaml), the canned block is inserted instead of the transcript."""

import difflib
import re
from typing import Optional


def _normalize(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # tolerate polite framing
    for prefix in ("please ", "can you ", "could you "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    if t.endswith(" please"):
        t = t[: -len(" please")]
    return t


class Snippets:
    def __init__(self, config):
        self.config = config

    def triggers(self) -> dict[str, str]:
        raw = self.config.get("snippets", {}) or {}
        return {str(k): str(v) for k, v in raw.items()}

    def match(self, transcript: str) -> Optional[str]:
        """Return snippet text if the utterance IS a trigger phrase."""
        norm = _normalize(transcript)
        if not norm:
            return None
        best, best_ratio = None, 0.0
        for trigger, body in self.triggers().items():
            trig_norm = _normalize(trigger)
            if not trig_norm:
                continue
            if norm == trig_norm:
                return self._body(body)
            ratio = difflib.SequenceMatcher(a=norm, b=trig_norm).ratio()
            if ratio > best_ratio:
                best, best_ratio = body, ratio
        if best is not None and best_ratio >= 0.85:
            return self._body(best)
        return None

    @staticmethod
    def _body(body: str) -> str:
        # YAML block scalars keep a trailing newline; don't paste it.
        return body[:-1] if body.endswith("\n") else body
