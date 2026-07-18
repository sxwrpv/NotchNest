"""Post-ASR cleanup: LLM pass with a rule-based fallback, plus the
Command Mode rewrite prompt."""

import logging
import re
from typing import Optional

from .llm import LLMRouter, LLMUnavailable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- rule-based

_SPOKEN_PUNCT = [
    # English
    (r"\bnew paragraph\b", "\n\n"),
    (r"\bnew line\b", "\n"),
    (r"\bexclamation (?:point|mark)\b", "!"),
    (r"\bquestion mark\b", "?"),
    (r"\bsemicolon\b", ";"),
    (r"\bcolon\b", ":"),
    (r"\bcomma\b", ","),
    (r"\bperiod\b", "."),
    (r"\bfull stop\b", "."),
    (r"\bopen quote\b", "“"),
    (r"\bclose quote\b", "”"),
    (r"\bdash\b", " — "),
    # Russian
    (r"\bновый абзац\b", "\n\n"),
    (r"\bновая строка\b", "\n"),
    (r"\bс новой строки\b", "\n"),
    (r"\bвосклицательный знак\b", "!"),
    (r"\bвопросительный знак\b", "?"),
    (r"\bточка с запятой\b", ";"),
    (r"\bдвоеточие\b", ":"),
    (r"\bзапятая\b", ","),
    (r"\bточка\b", "."),
    (r"\bтире\b", " — "),
]

# English fillers + unambiguous Russian fillers (э-э, эм, гм and the phrase
# "как бы"). Context-dependent Russian fillers (ну, короче, типа, вот) are
# deliberately NOT stripped here — they are real words in many sentences;
# the LLM prompt handles them with context.
_FILLERS = re.compile(
    r"\b(?:um+|uh+|erm+|ahh+|hmm+|mhm+|you know|i mean"
    r"|э-?э+|э+|эм+|гм+|мм+|как бы)\b[,.]?\s*",
    re.IGNORECASE,
)


def rule_based_cleanup(text: str) -> str:
    """Deterministic fallback used when no LLM is available."""
    t = " " + (text or "").strip() + " "
    t = _FILLERS.sub(" ", t)
    for pat, repl in _SPOKEN_PUNCT:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)
    # tidy space around punctuation
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,.;:!?])(\w)", r"\1 \2", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = t.strip()
    # capitalize sentence starts (any lowercase letter — Latin or Cyrillic)
    # and the lone English pronoun "i"
    t = re.sub(r"\bi\b", "I", t)
    t = re.sub(
        r"(^|[.!?]\s+|\n)([^\W\d_])",
        lambda m: m.group(1) + m.group(2).upper(),
        t,
    )
    if t and t[-1] not in ".!?\n:;,”":
        t += "."
    return t


# ------------------------------------------------------------------ prompts

BASE_SYSTEM = """You are a dictation post-processor. You receive the raw output of speech-to-text and rewrite it as clean written text.

Rules:
- The transcript may be in any language (often English or Russian). ALWAYS write your output in the SAME language as the transcript. Never translate.
- Remove filler words. English: um, uh, er, hmm, "you know", "I mean", meaningless "like". Russian: э-э, эм, ну, как бы, короче, типа, вот, значит — remove them only when they are meaningless fillers, keep them when they carry real meaning (e.g. "короче на два метра", "типа данных").
- Remove stutters and false starts; when the speaker corrects themselves, keep only their final intent (e.g. "send it Monday, no wait, Tuesday" -> "send it Tuesday").
- Add correct punctuation, capitalization, and paragraph breaks inferred from the natural speech. The speaker does NOT have to dictate punctuation.
- If the speaker DOES say punctuation words ("comma", "period", "question mark", "new line", "new paragraph" / "запятая", "точка", "вопросительный знак", "новая строка", "новый абзац"), convert them into the actual punctuation or formatting.
- Normalize obvious formatting: times like "3 pm" -> "3 PM", spelled-out simple numbers where a numeral is clearly more natural ("twenty five percent" -> "25%", "двадцать пять процентов" -> "25%").
- If the speaker clearly enumerates items ("first... second... third" or "number one... number two" / "во-первых... во-вторых", or a run of short parallel items), format them as a numbered or bulleted list, one item per line.
- Never answer questions in the transcript, never add new content, never comment on the text. Preserve the speaker's wording and meaning otherwise.
- You are a text filter, NOT a chat assistant. NEVER respond conversationally, NEVER ask the speaker anything, NEVER remark on typos, garbled words, or unclear text, NEVER apologize or refuse. There is no one to talk to — your output goes directly into a document.
- If the transcript is very short, strange, or unclear (even a single word or a name), output it as-is with sensible capitalization — do not comment on it.
- Output ONLY the cleaned text. No preamble, no quotes around it, no markdown fences."""

COMMAND_SYSTEM = """You rewrite text according to a spoken instruction.
- Apply the instruction faithfully; preserve the meaning of the original unless the instruction says otherwise.
- ALWAYS keep the rewritten text in the SAME language as the original text (the instruction's language does not matter). Never translate unless the instruction explicitly asks for a translation.
- Output ONLY the rewritten text. No preamble, no explanation, no quotes around it, no markdown fences."""


def build_cleanup_system(style_instructions: Optional[str], dictionary_terms: list[str]) -> str:
    parts = [BASE_SYSTEM]
    if style_instructions:
        parts.append(f"Tone: {style_instructions}")
    if dictionary_terms:
        terms = ", ".join(dictionary_terms[:60])
        parts.append(
            "Preferred spellings — if a word in the transcript sounds like one of "
            f"these terms, use this exact spelling: {terms}"
        )
    return "\n".join(parts)


# Assistant meta-talk markers. A marker only disqualifies the output if it
# does NOT also appear in the input — "I fixed a typo in the readme" must
# survive cleanup even though it contains "typo".
_META_MARKERS = (
    # English
    "as an ai",
    "as a language model",
    "i cannot",
    "i can't",
    "i'm sorry",
    "i am sorry",
    "i'm not sure",
    "here is the cleaned",
    "here's the cleaned",
    "typo",
    "please provide",
    "could you please",
    "please clarify",
    "clarify",
    "unclear",
    "no text provided",
    "cannot assist",
    "seems there might",
    # Russian
    "опечатка",
    "предоставьте",
    "уточните",
    "не могу помочь",
    "непонятно, что",
)


def _cyrillic_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    return cyr / len(letters)


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def _sane(raw: str, out: str) -> bool:
    """Reject LLM outputs that are assistant chatter rather than a cleanup of
    the transcript. Better to paste the raw ASR text than fake dialogue."""
    if not out or not out.strip():
        return False
    # runaway length (hallucinated continuation)
    if len(out) > max(3 * len(raw) + 80, len(raw) + 600):
        return False
    # collapsed content: cleanup strips fillers, it doesn't delete the message
    if len(raw.split()) > 12 and len(out) < 0.3 * len(raw):
        return False
    low, raw_low = out.lower(), raw.lower()
    for marker in _META_MARKERS:
        if marker in low and marker not in raw_low:
            return False
    # language mismatch: mostly-Cyrillic speech must not come back Latin-only
    # (and vice versa) — a classic symptom of the model replying *about* the
    # text instead of cleaning it
    r_in, r_out = _cyrillic_ratio(raw), _cyrillic_ratio(out)
    if r_in > 0.6 and r_out < 0.1:
        return False
    if r_in < 0.1 and r_out > 0.6:
        return False
    # the cleaned text must largely reuse the transcript's own words;
    # meta-chatter is built from new words
    raw_words = _content_words(raw)
    out_words = _content_words(out)
    if len(raw_words) >= 4 and out_words:
        overlap = len(out_words & raw_words) / len(out_words)
        if overlap < 0.35:
            return False
    return True


def _strip_wrapping(out: str) -> str:
    t = out.strip()
    if t.startswith("```") and t.endswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    if len(t) > 1 and t[0] in "\"'“" and t[-1] in "\"'”" and t[0] not in t[1:-1]:
        t = t[1:-1].strip()
    return t


class CleanupEngine:
    def __init__(self, config, router: LLMRouter):
        self.config = config
        self.router = router

    def clean(
        self,
        raw: str,
        style_instructions: Optional[str] = None,
        dictionary_terms: Optional[list[str]] = None,
    ) -> tuple[str, str]:
        """Returns (cleaned_text, engine_name). engine_name values:
        backend name (ollama/mlx), "rules" (LLM unavailable),
        "rules-short" (utterance too short to bother the LLM),
        "rules-guard" (LLM output rejected by the sanity check)."""
        raw = (raw or "").strip()
        if not raw:
            return "", "none"
        if not self.config.get("llm.cleanup_enabled", True):
            return rule_based_cleanup(raw), "rules-off"
        # 1-2 word utterances: nothing to clean — skipping the LLM avoids
        # both latency and the model's temptation to chat about the input
        if len(raw.split()) <= 2:
            return rule_based_cleanup(raw), "rules-short"
        system = build_cleanup_system(style_instructions, dictionary_terms or [])
        try:
            out, backend = self.router.generate(system, raw)
            out = _strip_wrapping(out)
            if _sane(raw, out):
                return out, backend
            log.warning(
                "LLM cleanup output failed sanity check (%r for input %r); using rules",
                out[:120],
                raw[:120],
            )
            return rule_based_cleanup(raw), "rules-guard"
        except LLMUnavailable as e:
            log.info("LLM unavailable (%s); using rule-based cleanup", e)
        return rule_based_cleanup(raw), "rules"

    def rewrite(self, selection: str, instruction: str) -> tuple[str, str]:
        """Command Mode. Raises LLMUnavailable when no backend is running."""
        user = f"Instruction: {instruction.strip()}\n\nText:\n{selection}"
        out, backend = self.router.generate(COMMAND_SYSTEM, user)
        out = _strip_wrapping(out)
        if not out:
            raise LLMUnavailable("empty rewrite")
        return out, backend
