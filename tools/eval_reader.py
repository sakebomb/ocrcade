#!/usr/bin/env python3
"""Score a code reader against a labelled sample set. Run on kepler.

    python3 tools/eval_reader.py ~/ocrcade-eval/handwriting --reader hailo-whole --run

Per sample: character error rate (CER), exact match, latency, and with --run whether the
read code earns the flag (sandbox + validator, as in a real submission). Writes every read
and score to `<set>/results/<reader>-<timestamp>.json` so runs can be compared.
Workflow and metric definitions: docs/EVAL.md.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import Sample, load_samples  # noqa: E402
from evaluation.metrics import cer, exact_match  # noqa: E402
from evaluation.readers import READERS, make_reader  # noqa: E402
from vision import load_config  # noqa: E402


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score a code reader against a labelled set.")
    p.add_argument("set_dir", type=Path)
    p.add_argument("--reader", required=True, choices=READERS)
    p.add_argument("--run", action="store_true",
                   help="also run each read in the sandbox and validate it (kepler only)")
    p.add_argument("--limit", type=int, help="only the first N samples")
    return p.parse_args()


def _passes(sample: Sample, code: str | None) -> bool:
    """Would this read earn the flag? Same path as a real submission."""
    if not code:
        return False
    from game.output_validator import validate
    from game.sandbox_runner import run as sandbox_run

    result = sandbox_run(sample.puzzle, sample.lang, code)
    if result["timed_out"]:
        return False
    ok, _ = validate(sample.puzzle, result["stdout"])
    return ok


def _score(reader, sample: Sample, set_dir: Path, run: bool) -> dict:
    image = cv2.imread(str(set_dir / sample.image))
    if image is None:
        return {"image": sample.image, "error": "cannot read image"}
    r = reader.read(image)
    row = {"image": sample.image, "puzzle": sample.puzzle, "writer": sample.writer,
           "medium": sample.medium, "cer": round(cer(sample.truth, r.code), 4),
           "exact": exact_match(sample.truth, r.code), "latency_s": round(r.latency_s, 2),
           "abstained": r.code is None, "reason": r.reason, "read": r.code}
    if run:
        row["passes"] = _passes(sample, r.code)
    return row


def _summary(rows: list[dict], run: bool) -> dict:
    scored = [r for r in rows if "error" not in r]
    if not scored:
        return {"samples": 0, "errors": len(rows)}
    lat = [r["latency_s"] for r in scored]
    out = {"samples": len(scored), "errors": len(rows) - len(scored),
           "mean_cer": round(statistics.mean(r["cer"] for r in scored), 4),
           "exact_rate": round(sum(r["exact"] for r in scored) / len(scored), 3),
           "abstain_rate": round(sum(r["abstained"] for r in scored) / len(scored), 3),
           "latency_p50_s": round(statistics.median(lat), 2),
           "latency_max_s": round(max(lat), 2)}
    if run:
        out["pass_rate"] = round(sum(r["passes"] for r in scored) / len(scored), 3)
    return out


def _print_row(row: dict) -> None:
    if "error" in row:
        print(f"{row['image']:<24} ERROR {row['error']}")
        return
    verdict = "abstain" if row["abstained"] else ("exact" if row["exact"] else "")
    passes = "" if "passes" not in row else ("PASS" if row["passes"] else "fail")
    print(f"{row['image']:<24} cer={row['cer']:<6.3f} {row['latency_s']:>5.1f}s "
          f"{passes:<4} {verdict}")


def main() -> int:
    args = _args()
    samples = load_samples(args.set_dir)[: args.limit]
    if not samples:
        print(f"no samples in {args.set_dir}", file=sys.stderr)
        return 1
    reader = make_reader(args.reader, load_config()["vlm"])
    rows = []
    for sample in samples:
        rows.append(_score(reader, sample, args.set_dir, args.run))
        _print_row(rows[-1])
    summary = _summary(rows, args.run)
    out_dir = args.set_dir / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{args.reader}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"reader": args.reader, "summary": summary, "rows": rows,
                               "samples": [asdict(s) for s in samples]}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
