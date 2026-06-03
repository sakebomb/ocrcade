"""OCRcade v2 vision package: capture → gate → VLM read → CardReading."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_config(path: str | Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)
