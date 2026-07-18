#!/usr/bin/env python3
"""Full multilingual verification: whisper-large-v3-turbo on English and
Russian fixtures (latency + accuracy), Russian LLM cleanup, Russian Command
Mode rewrite, and memory footprint. Run after weights are cached:

    HF_HUB_OFFLINE=1 .venv/bin/python -u scripts/verify_multilingual.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import psutil
import soundfile as sf

from murmur.cleanup import CleanupEngine, rule_based_cleanup
from murmur.config import Config
from murmur.llm import LLMRouter
from murmur.transcriber import MLXWhisperTranscriber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EN_WAV = os.path.join(ROOT, "tests", "fixtures", "sample16k.wav")
RU_WAV = os.path.join(ROOT, "tests", "fixtures", "sample_ru16k.wav")


def rss_mb():
    return psutil.Process(os.getpid()).memory_info().rss / 1e6


def load(path):
    data, sr = sf.read(path)
    assert sr == 16000
    return data.astype(np.float32)


def main():
    import tempfile

    tmp = tempfile.mkdtemp()
    cfgp = os.path.join(tmp, "config.yaml")
    with open(cfgp, "w") as f:
        f.write("llm:\n  backend: mlx\n")
    cfg = Config(cfgp)

    print(f"RSS baseline: {rss_mb():.0f} MB")
    t = MLXWhisperTranscriber("large-v3-turbo")
    print("model:", t.model_repo)

    en = load(EN_WAV)
    ru = load(RU_WAV)

    # cold-ish first decode (includes weight load + Metal compile)
    t0 = time.time()
    en_text = t.transcribe(en, language="auto")
    t_first = time.time() - t0
    print(f"\nEN first decode ({len(en)/16000:.1f}s audio): {t_first:.2f}s")
    print(f"EN text: {en_text!r}  [lang={t.last_language}]")
    print(f"RSS after ASR load: {rss_mb():.0f} MB")

    # warm English decode
    t0 = time.time()
    en_text2 = t.transcribe(en, language="auto")
    print(f"EN warm decode: {time.time()-t0:.2f}s -> {en_text2!r}")

    # Russian decode (auto-detect)
    t0 = time.time()
    ru_text = t.transcribe(ru, language="auto")
    t_ru = time.time() - t0
    print(f"\nRU decode ({len(ru)/16000:.1f}s audio): {t_ru:.2f}s  [lang={t.last_language}]")
    print(f"RU raw: {ru_text!r}")
    has_cyrillic = any("а" <= ch.lower() <= "я" for ch in ru_text)
    print("contains Cyrillic:", has_cyrillic)

    # warm Russian decode
    t0 = time.time()
    t.transcribe(ru, language="auto")
    print(f"RU warm decode: {time.time()-t0:.2f}s")

    # rule-based fallback on the Russian raw (sanity)
    print("\nRU rule-based cleanup:", repr(rule_based_cleanup(ru_text)))

    # LLM cleanup in Russian
    router = LLMRouter(cfg)
    engine = CleanupEngine(cfg, router)
    t0 = time.time()
    cleaned, backend = engine.clean(ru_text, style_instructions=None, dictionary_terms=["Murmur"])
    print(f"\nRU LLM cleanup via {backend} in {time.time()-t0:.2f}s:")
    print(repr(cleaned))
    print(f"RSS peak (ASR turbo + LLM resident): {rss_mb():.0f} MB")

    # Command Mode rewrite in Russian
    t0 = time.time()
    rewritten, backend2 = engine.rewrite(
        "привет, скинь мне файл когда сможешь, спасибо", "сделай это более формальным"
    )
    print(f"\nRU command rewrite via {backend2} in {time.time()-t0:.2f}s:")
    print(repr(rewritten))

    # unload LLM, settled RSS
    router.mlx.unload()
    time.sleep(0.5)
    print(f"\nRSS settled (ASR resident, LLM unloaded): {rss_mb():.0f} MB")
    print("\nVERIFY_DONE")


if __name__ == "__main__":
    main()
