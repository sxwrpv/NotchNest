#!/usr/bin/env python3
"""Measures Murmur's real RSS footprint on this machine:
  1. idle    - process up, config loaded, mic stream open, ASR model warmed
  2. peak    - immediately after one ASR decode + one LLM cleanup pass
  3. settled - after the MLX LLM idle-unload has run

Run with:  .venv/bin/python scripts/measure_memory.py
No mic/hotkey interaction required — uses the synthesized fixture WAV.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import psutil
import soundfile as sf

from murmur.config import Config
from murmur.controller import Controller

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "sample16k.wav")


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1e6


def main():
    tmp_cfg = "/tmp/murmur_measure_config.yaml"
    with open(tmp_cfg, "w") as f:
        f.write("llm:\n  backend: mlx\n  ollama:\n    keep_alive: 5m\n")
    cfg = Config(tmp_cfg)
    db = "/tmp/murmur_measure.db"
    if os.path.exists(db):
        os.remove(db)

    print(f"baseline RSS before any Murmur objects: {rss_mb():.1f} MB")
    ctrl = Controller(cfg, db)
    ctrl.audio.start_if_always_on()
    print(f"RSS after Controller + mic stream open:  {rss_mb():.1f} MB")

    t0 = time.time()
    ctrl.transcriber().warmup()
    print(f"RSS after ASR ({cfg.get('asr.model')}) warmup ({time.time()-t0:.1f}s): {rss_mb():.1f} MB")
    idle_rss = rss_mb()

    data, sr = sf.read(FIXTURE)
    data = data.astype(np.float32)
    t0 = time.time()
    raw = ctrl.transcriber().transcribe(data, initial_prompt=ctrl.dictionary.initial_prompt())
    asr_time = time.time() - t0
    print(f"ASR decode ({len(data)/sr:.1f}s audio) in {asr_time:.2f}s -> {raw!r}")
    print(f"RSS after ASR decode: {rss_mb():.1f} MB")

    t0 = time.time()
    try:
        final, engine = ctrl.cleanup.clean(raw, style_instructions="neutral tone",
                                            dictionary_terms=ctrl.dictionary.all_terms())
        llm_time = time.time() - t0
        print(f"Cleanup via {engine} in {llm_time:.2f}s -> {final!r}")
    except Exception as e:
        print(f"Cleanup pass failed: {e}")
    peak_rss = rss_mb()
    print(f"RSS peak (ASR + LLM both resident): {peak_rss:.1f} MB")

    print("Waiting for MLX LLM idle-unload (llm.mlx.idle_unload_s + margin)...")
    ctrl.router.mlx.unload()  # force it for this measurement instead of waiting 5 min
    time.sleep(0.5)
    settled_rss = rss_mb()
    print(f"RSS after LLM unload (settled/idle):  {settled_rss:.1f} MB")

    print("\n--- summary ---")
    print(f"idle (ASR warm, LLM unloaded): {idle_rss:.1f} MB")
    print(f"peak (ASR + LLM resident):     {peak_rss:.1f} MB")
    print(f"settled (post idle-unload):    {settled_rss:.1f} MB")


if __name__ == "__main__":
    main()
