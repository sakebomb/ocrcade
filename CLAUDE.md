# OCRcade — agent guide

A coding-arcade game. A player shows a printed puzzle card and then their code (screen or
paper) to a camera. The Pi reads the code, runs it in a sandbox, checks the output, and
shows the result on a monitor. Every puzzle is a path to its flag. The operator guide
(`docs/SYSTEM.md`) is the full explanation; this file is what an agent needs to work safely.

## Current direction

- **Code readers are Claude Sonnet, then Hailo OCR.** `vlm.code_readers` tries LiteLLM
  (`claude-sonnet-5-5`) first and `hailo_ocr` (PaddleOCR on the Hailo-10H) when that
  reader is down, slow, rate-limited, or over budget. Claude declines an emphatic
  "transcribe EXACTLY" prompt (`content_filter`, no text); test any prompt change against
  it on kepler. The Hailo Qwen2-VL-2B is not a code reader:
  at its fixed 336x336 input it only reads the puzzle number. Ollama is an optional reader.
- **Handwriting is the main accuracy goal.** Measure it with a labelled set of photos before
  tuning anything; the gate thresholds were validated on screen cards only (light text on a
  dark screen), not on ink on paper.
- **Scoring to RootTheBox is not in use** for the current demo. Results only show on the display.

## Layout

| Path | What it is |
|------|-----------|
| `daemon.py` | Vision state machine: card marker → code frame → read → read-back → submit |
| `server.py` | Flask exec/display server (:5000): sandbox run, validate, display |
| `vision/` | Capture, frame gate, ArUco markers, prompts, reader client, backends (`litellm_vlm`, `hailo_ocr`, `ollama_vlm`, `hailo_vlm`) |
| `game/` | Sandbox runner (bwrap), output validators, maze generator, roster match, RTB submitter |
| `display/` | Framebuffer UI (`/dev/fb0`) |
| `hw/button.py` | GPIO confirm button (lgpio) |
| `challenges/` | Camera mini-games: Konami (puzzle 5), Fruit Ninja (puzzle 6, Hailo pose model) |
| `puzzles/levelN/` | Per-puzzle data, mounted read-only at `/puzzle` in the sandbox |
| `tools/` | Puzzle-card generator, VLM smoke test over a folder of JPEGs, sandbox test |
| `deploy/` | systemd units and `deploy.sh` |

## Commands

```bash
ruff check .                                    # lint (CI)
python -m pytest tests/ -q                      # unit tests (CI), no device needed
python tools/phase1_smoke.py <dir-of-jpgs>      # run the real VLM client over saved frames
deploy/deploy.sh [REF]                          # push REF to kepler, restart, health-check
```

## The device: kepler

| Fact | Value |
|------|-------|
| Address | `<pi-host>` (mDNS `kepler.local`); SSH as `sakebomb` |
| Board / OS | Raspberry Pi 5, 8 GB, Debian 13 (trixie) |
| Accelerator | AI HAT+ 2: Hailo-10H, firmware and `h10-hailort` 5.1.1, Python `hailo_platform` 5.1.1 with `hailo_platform.genai` |
| Also installed | `hailort` 4.23 (the Hailo-8 runtime). Check both after any apt upgrade |
| Models | `~/Qwen2-VL-2B-Instruct.hef`, `~/Qwen2-1.5B-Instruct.hef`; vision HEFs in `/usr/share/hailo-models/` (`*_h10.hef` are for this chip) |
| Camera | IMX500 (2028x1520 used) |
| Services | `ocrcade-exec`, `ocrcade-vision` (system python, not a venv) |
| Run dir | `~/ocrcade`: a git checkout on branch `deploy` |
| Logs | `journalctl -u ocrcade-vision` / `-u ocrcade-exec`. The current code saves no camera frames; `~/ocrcade-logs/` holds 482 screen-card frames from the earlier version |

## Invariants

- **Never commit event data:** `.env`, `teams.json`, `flag_config.json`, `konami_config.json`,
  `flags/*.txt`, `puzzles/level*/flag*.txt`. `.gitignore` covers them; keep it that way.
- **Never edit code on kepler.** Change it here and run `deploy/deploy.sh`. A hand edit
  blocks the next deploy by design.
- **Device libraries import lazily in `vision/` and `hw/`** (`picamera2`, `hailo_platform`,
  `lgpio`) so that code lints and tests off the Pi. The `challenges/` scripts import them
  at the top and only run on the Pi; no test imports them.
- **The `readable:false` abstention in `vision/prompts.py` is load-bearing.** Without it
  the model invents a program for a blank frame. Keep an abstain path in any new reader.
- **Player code is untrusted.** Every run goes through `game/sandbox_runner.py` (bwrap, no
  network, user namespace, ulimits). The puzzle folder at `/puzzle` is the intended route
  to each flag (level 1 prints `flag1.txt` from it); nothing else on the host should be
  reachable. Treat any sandbox change as security-sensitive.
- **Puzzle 1's flag lives in two files that must match:** `puzzles/level1/flag1.txt` (what
  the player prints from `/puzzle`) and `flags/flag1.txt` (what the validator checks).
- **The Hailo chip is shared.** Fruit Ninja's pose pipeline and the VLM both use it, and
  whether they can run at the same time is untested. Check `sudo fuser -v /dev/hailo0`
  before an experiment.

## Conventions

Branch from `main` (`feat/`, `fix/`, `refactor/`, `test/`, `docs/`), small PRs, squash
merge, CI must pass. Style: ruff, line length 100, typed signatures. See `CONTRIBUTING.md`.
