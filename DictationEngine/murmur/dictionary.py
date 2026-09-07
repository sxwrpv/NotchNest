"""Personal dictionary.

Two mechanisms, as specified:
  (a) terms are injected into whisper's initial_prompt to bias recognition,
      and given to the cleanup LLM as preferred spellings;
  (b) a find/replace safety net runs on the final text.

Sources: manual entries in config.yaml, plus auto-learned terms/replacements
stored in SQLite and derived from "Fix Last Transcription…" corrections.
"""

import difflib
import logging
import re
import sqlite3
import time
from typing import Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learned_replacements (
    src TEXT PRIMARY KEY COLLATE NOCASE,
    dst TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS learned_terms (
    term TEXT PRIMARY KEY,
    ts REAL NOT NULL
);
"""


class PersonalDictionary:
    def __init__(self, config, db_path: str):
        self.config = config
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        return conn

    # -- terms -----------------------------------------------------------
    def manual_terms(self) -> list[str]:
        entries = self.config.get("dictionary.entries", []) or []
        return [str(e).strip() for e in entries if str(e).strip()]

    def learned_terms(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT term FROM learned_terms ORDER BY ts DESC LIMIT 100"
            ).fetchall()
        return [r[0] for r in rows]

    def all_terms(self) -> list[str]:
        seen, out = set(), []
        for t in self.manual_terms() + self.learned_terms():
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    def initial_prompt(self) -> Optional[str]:
        """Bias string for whisper's initial_prompt (kept short: the prompt
        window is ~224 tokens and long prompts dilute the bias)."""
        terms = self.all_terms()
        if not terms:
            return None
        # A bare comma-separated term list is the documented way to bias
        # Whisper. A leading word ("Glossary:") gives the decoder an English
        # sentence to echo, and on unclear audio it did exactly that —
        # emitting "Glossary" hundreds of times instead of the dictation.
        return ", ".join(terms)[:600]

    # -- replacements ------------------------------------------------------
    def replacement_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        cfg = self.config.get("dictionary.replacements", {}) or {}
        for k, v in cfg.items():
            out[str(k)] = str(v)
        with self._conn() as c:
            rows = c.execute(
                "SELECT src, dst FROM learned_replacements WHERE active=1"
            ).fetchall()
        for src, dst in rows:
            out.setdefault(src, dst)
        return out

    def apply_replacements(self, text: str) -> str:
        """Case-insensitive whole-word/phrase replacement safety net."""
        result = text
        for src, dst in self.replacement_map().items():
            if not src or src.lower() == dst.lower() and src == dst:
                continue
            pattern = r"(?<![\w])" + re.escape(src) + r"(?![\w])"
            result = re.sub(pattern, dst.replace("\\", "\\\\"), result, flags=re.IGNORECASE)
        return result

    def add_term(self, term: str) -> None:
        term = term.strip()
        if not term:
            return
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO learned_terms(term, ts) VALUES (?, ?)",
                (term, time.time()),
            )
        log.info("dictionary term added: %s", term)

    # -- auto-learning from corrections -------------------------------------
    def record_correction(self, original: str, corrected: str) -> list[tuple[str, str]]:
        """Store a correction and mine phrase-level replacements from the
        diff. Returns the (src, dst) pairs learned this round."""
        original = (original or "").strip()
        corrected = (corrected or "").strip()
        if not original or not corrected or original == corrected:
            return []
        with self._conn() as c:
            c.execute(
                "INSERT INTO corrections(ts, original, corrected) VALUES (?, ?, ?)",
                (time.time(), original, corrected),
            )
        pairs = self._mine_pairs(original, corrected)
        if not self.config.get("dictionary.auto_learn", True):
            return []
        learned = []
        with self._conn() as c:
            for src, dst in pairs:
                c.execute(
                    """INSERT INTO learned_replacements(src, dst, count, active)
                       VALUES (?, ?, 1, 1)
                       ON CONFLICT(src) DO UPDATE SET
                         dst=excluded.dst, count=count+1, active=1""",
                    (src, dst),
                )
                learned.append((src, dst))
                # words with letters and interesting casing become bias terms
                for word in dst.split():
                    w = word.strip(".,;:!?\"'()")
                    if len(w) >= 3 and not w.islower() and any(ch.isalpha() for ch in w):
                        c.execute(
                            "INSERT OR REPLACE INTO learned_terms(term, ts) VALUES (?, ?)",
                            (w, time.time()),
                        )
        if learned:
            log.info("auto-learned replacements: %s", learned)
        return learned

    @staticmethod
    def _mine_pairs(original: str, corrected: str) -> list[tuple[str, str]]:
        a, b = original.split(), corrected.split()
        # Lower-cased alignment means SequenceMatcher treats a pure-casing fix
        # ("polycopy" -> "PolyCopy") as an "equal" block, not a "replace" one —
        # handle both tags so casing-only corrections are still learned.
        sm = difflib.SequenceMatcher(a=[w.lower() for w in a], b=[w.lower() for w in b])
        pairs = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    wa, wb = a[i1 + k], b[j1 + k]
                    if wa == wb:
                        continue
                    src = wa.strip(".,;:!?")
                    dst = wb.strip(".,;:!?")
                    if src and dst and len(src) >= 2:
                        pairs.append((src, dst))
                continue
            if tag != "replace":
                continue
            if (i2 - i1) > 4 or (j2 - j1) > 4:  # only small, local edits
                continue
            src = " ".join(a[i1:i2]).strip(".,;:!?")
            dst = " ".join(b[j1:j2]).strip(".,;:!?")
            if not src or not dst or src.lower() == dst.lower():
                # pure casing fixes are still useful
                if src and dst and src != dst:
                    pairs.append((src, dst))
                continue
            if len(src) < 2 or len(dst) < 2:
                continue
            pairs.append((src, dst))
        return pairs

    def last_corrections(self, n: int = 5) -> list[tuple[str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT original, corrected FROM corrections ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        return rows

    # -- management (Settings GUI) -------------------------------------------
    def learned_replacements(self) -> list[tuple[str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT src, dst FROM learned_replacements WHERE active=1 ORDER BY src"
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def remove_learned_term(self, term: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM learned_terms WHERE term = ?", (term,))
        log.info("learned term removed: %s", term)

    def remove_learned_replacement(self, src: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM learned_replacements WHERE src = ?", (src,))
        log.info("learned replacement removed: %s", src)
