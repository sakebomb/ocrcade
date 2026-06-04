"""OCRcade v2 vision package: capture → gate → VLM read → CardReading."""
from __future__ import annotations

from pathlib import Path


def load_config(path: str | Path | None = None) -> dict:
    # Lazy import: keeps the package importable without PyYAML (e.g. lightweight
    # CI importing pure-Python submodules like vision.cardspec).
    import yaml

    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)
