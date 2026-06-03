# Contributing

## Workflow
- Branch from `main` (`feat/`, `fix/`, `refactor/`, `test/`). No direct commits to `main`.
- Small PRs. Squash merge.
- CI (ruff + byte-compile) must pass.

## Code style
- `ruff` + `black` + `isort` (config in `pyproject.toml`), line length 100.
- Type-annotate signatures. Prefer small, focused modules.
- Immutable data where practical (`CardReading` is frozen).

## Local checks
```bash
ruff check . && black --check . && isort --check .
python -m compileall -q vision game display *.py
```

## Device-only modules
`vision/capture.py` (picamera2) and `vision/backends/hailo_vlm.py` (hailo_platform)
import device libraries **lazily** so the rest of the code lints and runs off the Pi.
Keep it that way — no top-level device imports.

## Testing a VLM change
Use `tools/phase1_smoke.py <dir-of-card-jpgs>` to run the real client over sample
cards without a camera. Add new tricky cards to the sample set when you fix a misread.
