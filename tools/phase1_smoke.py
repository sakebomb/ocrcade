#!/usr/bin/env python3
"""Phase-1 smoke test: drive the real VLMClient spine over the sample cards.
Run from Jupiter (Ollama is local). Proves config -> client -> backend -> CardReading."""
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from vision import load_config
from vision.vlm_client import from_config

CARDS = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/sakebomb/homelab/tasks/scratch/ocrcade-cards"

cfg = load_config()
client = from_config(cfg["vlm"])
print(f"primary={client.primary.name} available={client.primary.available()} "
      f"fallback={client.fallback.name if client.fallback else None} "
      f"degraded={client.degraded}\n")

for img_path in sorted(glob.glob(os.path.join(CARDS, "*.jpg"))):
    img = cv2.imread(img_path, cv2.IMREAD_COLOR)
    r = client.read_card(img)
    print("=" * 64)
    print(f"{os.path.basename(img_path)}")
    print(f"  readable={r.readable} backend={r.backend} ({r.latency_s:.1f}s)")
    if r.readable:
        print(f"  P{r.puzzle} team={r.team!r} lang={r.lang} complete={r.is_complete_code_card()}")
        for line in str(r.code or "").splitlines():
            print(f"     | {line}")
    else:
        print(f"  reason: {r.reason}")
