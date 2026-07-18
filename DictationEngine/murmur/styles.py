"""Tone presets for the cleanup pass + per-app defaults keyed on the
frontmost application's bundle identifier."""

from typing import Optional

BUILTIN_PRESETS = {
    "neutral": "Keep the speaker's natural tone. Do not make the text more or less formal.",
    "formal": (
        "Use a professional, formal tone: complete sentences, no contractions, "
        "no slang, precise wording."
    ),
    "casual": (
        "Use a relaxed, casual tone: contractions are fine, keep it light and "
        "conversational."
    ),
}


class StyleManager:
    def __init__(self, config):
        self.config = config

    def presets(self) -> dict[str, str]:
        merged = dict(BUILTIN_PRESETS)
        for name, text in (self.config.get("styles.presets", {}) or {}).items():
            merged[str(name)] = str(text)
        return merged

    def resolve(
        self, bundle_id: Optional[str] = None, override: Optional[str] = None
    ) -> tuple[str, str]:
        """Returns (style_name, instruction_text) for the given frontmost app.
        `override` (from the pill's style switcher) wins over per-app mapping
        and the configured default."""
        per_app = self.config.get("styles.per_app", {}) or {}
        name = override
        if not name and bundle_id:
            name = per_app.get(bundle_id)
        if not name:
            name = self.config.get("styles.default", "neutral")
        presets = self.presets()
        if name not in presets:
            name = "neutral"
        return name, presets[name]

    def cycle_order(self) -> list:
        """Order the pill's style switcher cycles through. None = 'Auto'
        (per-app/default behavior), then built-ins, then custom presets."""
        names = list(BUILTIN_PRESETS.keys())
        for name in self.presets():
            if name not in names:
                names.append(name)
        return [None] + names

    def next_in_cycle(self, current: Optional[str]) -> Optional[str]:
        order = self.cycle_order()
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        return order[(idx + 1) % len(order)]
