#!/usr/bin/env python3
"""ASR speed/quality benchmark for one model, printed as JSON.

Run in a FRESH process per model so load-state doesn't leak:

    HF_HUB_OFFLINE=1 .venv/bin/python -u scripts/bench_asr.py <model-alias-or-repo>

Measures:
  cold_first_decode : process start -> first EN decode done (load + Metal compile)
  en_warm / ru_warm : median of 3 warm decodes per language
  evicted_decode    : EN decode after Qwen2.5-3B loads + generates once
                      (the "LLM eviction penalty" felt in live use)
Also prints the EN/RU transcripts for quality comparison.
"""

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import psutil
import soundfile as sf

from murmur.transcriber import MLXWhisperTranscriber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_wav(name):
    data, sr = sf.read(os.path.join(ROOT, "tests", "fixtures", name))
    assert sr == 16000
    return data.astype(np.float32)


def rss_mb():
    return psutil.Process(os.getpid()).memory_info().rss / 1e6


def main():
    model = sys.argv[1]
    en = load_wav("sample16k.wav")
    ru = load_wav("sample_ru16k.wav")
    t = MLXWhisperTranscriber(model)
    out = {"model": model, "repo": t.model_repo}

    t0 = time.time()
    en_text = t.transcribe(en, language="auto")
    out["cold_first_decode_s"] = round(time.time() - t0, 2)
    out["rss_after_load_mb"] = round(rss_mb())
    out["en_text"] = en_text

    times = []
    for _ in range(3):
        t0 = time.time()
        t.transcribe(en, language="auto")
        times.append(time.time() - t0)
    out["en_warm_s"] = round(statistics.median(times), 3)

    times = []
    for _ in range(3):
        t0 = time.time()
        ru_text = t.transcribe(ru, language="auto")
        times.append(time.time() - t0)
    out["ru_warm_s"] = round(statistics.median(times), 3)
    out["ru_text"] = ru_text
    out["ru_lang_detected"] = t.last_language

    # ---- eviction scenario: load the cleanup LLM, generate once, re-decode
    from mlx_lm import generate, load

    t0 = time.time()
    lm, tok = load("mlx-community/Qwen2.5-3B-Instruct-4bit")
    prompt = tok.apply_chat_template(
        [{"role": "user", "content": "Clean this: um hello there"}],
        tokenize=False,
        add_generation_prompt=True,
    )
    generate(lm, tok, prompt=prompt, max_tokens=40, verbose=False)
    out["llm_load_generate_s"] = round(time.time() - t0, 2)
    out["rss_with_llm_mb"] = round(rss_mb())

    t0 = time.time()
    t.transcribe(en, language="auto")
    out["evicted_decode_s"] = round(time.time() - t0, 3)

    t0 = time.time()
    t.transcribe(ru, language="auto")
    out["evicted_decode_ru_s"] = round(time.time() - t0, 3)

    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
