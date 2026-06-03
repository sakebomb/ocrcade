# OCRcade

A **coding-arcade game**: teams write code, show their screen (or paper) to an AI
camera, and the system **reads the code with a vision-language model**, runs it in a
sandbox, validates the output against a puzzle, and scores the team — live, on a
big display.

OCRcade v2 replaces the original Tesseract-OCR reader with a VLM. Tesseract was the
bottleneck: on real cards it produced `with opan('data.tt') as f:` from
`with open('data.txt') as f:`, and read one team name (`Megad00mer`) five different
ways. ~500 lines of the old codebase existed only to paper over those errors. A VLM
that returns structured JSON deletes nearly all of it.

## Architecture

```
 IMX500 camera ─► FrameGate ─► VLMClient ─► CardReading ─► Exec server ─► Display
 (kepler)         (local)      (selector)   (JSON)         (sandbox +      (scoreboard)
                                                            validate + RTB)
```

**Reading the code (the hard part):**

| Role | Backend | Notes |
|------|---------|-------|
| **Code reader** | Qwen2.5-VL-7B via **Ollama on a GPU** | Character-perfect on clean cards. Must abstain (`readable:false`) on bad frames. |
| **Coarse fallback** | Qwen2-VL-2B on a **Hailo-10H** | Fixed 336² input — reads only the large puzzle *number*. Used for routing + camera-challenge puzzles when the GPU is unreachable. **Not a code reader.** |
| **Gate** | OpenCV, local | Blur + stability + content. Only sharp, stable, text-bearing frames reach the VLM — which also stops the VLM hallucinating on junk. |

Both backends return the same immutable `CardReading`. The `readable` flag is the
safety gate: a confident-but-wrong read is worse than an honest "couldn't read," so
the reader is allowed (and prompted) to abstain.

**Offline behavior:** with no GPU reachable, the game still runs — attract screen,
routing, and the two camera-challenge puzzles (Konami, Fruit Ninja) work fully
local on the Hailo. Code puzzles (1–4) need the GPU link. For untethered code-play,
bring a portable GPU — not the 2B.

## Layout

```
config.yaml          endpoints + gating thresholds (single source of truth)
vision/
  schema.py          CardReading
  prompts.py         the validated read prompt (with abstention)
  vlm_client.py      VLMClient + reachability selector + from_config()
  gating.py          FrameGate (blur / stability / content)
  capture.py         IMX500 / picamera2 wrapper
  backends/
    ollama_vlm.py    GPU code reader
    hailo_vlm.py     Hailo coarse fallback
game/                sandbox runner, output validators, maze, RTB submitter, teams
display/             framebuffer display manager
server.py            Flask exec/display server
daemon.py            capture → gate → read → submit loop
```

## Setup

The vision pipeline + exec server run on a Raspberry Pi (`kepler`: Pi 5 + Hailo-10H
+ IMX500). The GPU VLM is reached over HTTP — it runs anywhere Ollama runs.

```bash
# on the Pi
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # hailo_platform + picamera2 come from the system image
# on the GPU host
ollama pull qwen2.5vl:7b
```

Edit `config.yaml` (Ollama URL/model, gating thresholds, camera rotation), then:

```bash
python3 server.py          # exec + display server (:5000)
python3 daemon.py          # vision loop
```

## Configuration

See `config.yaml`. Key knobs: `vlm.ollama.{url,model,num_ctx}` (**cap `num_ctx`**, or
the model overflows VRAM and falls back to CPU), `gating.{blur_min,stable_frames}`
(tune on the device), `camera.rotate`.

## License

Proprietary — see [LICENSE](LICENSE).
