"""A labelled sample set: one directory of JPEGs plus a JSON-lines manifest.

Each manifest line is one `Sample`. The truth is the exact code the person copied, so a
sample needs no separate label file. Sets live on kepler (default `~/ocrcade-eval/`) and
stay out of git: they are photos of people's handwriting.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

MANIFEST = "manifest.jsonl"
_WRITER = re.compile(r"[a-z0-9_]{1,32}")


@dataclass(frozen=True)
class Sample:
    image: str      # file name inside the set directory
    truth: str      # the code that was written down
    puzzle: int
    lang: str
    writer: str     # who wrote it, a short token, so results can be split by hand
    medium: str     # "paper" or "screen"
    blur: float     # variance-of-Laplacian of the saved frame (higher = sharper)


def load_samples(set_dir: str | Path) -> list[Sample]:
    path = Path(set_dir) / MANIFEST
    if not path.exists():
        return []
    names = {f.name for f in fields(Sample)}
    samples = []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if set(record) != names:
                raise ValueError(f"fields {sorted(record)} != {sorted(names)}")
            samples.append(Sample(**record))
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise ValueError(f"{path} line {n}: {e}") from e
    return samples


def append_sample(set_dir: str | Path, sample: Sample) -> None:
    path = Path(set_dir) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(sample)) + "\n")


def next_name(set_dir: str | Path, puzzle: int, writer: str) -> str:
    """The next free base name for this puzzle and writer, e.g. `p2-alice-003`."""
    if not _WRITER.fullmatch(writer):
        raise ValueError(f"writer must match {_WRITER.pattern}: {writer!r}")
    prefix = f"p{puzzle}-{writer}-"
    taken = [s.image for s in load_samples(set_dir) if s.image.startswith(prefix)]
    return f"{prefix}{len(taken) + 1:03d}"
