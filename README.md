# OCRcade

OCRcade is a coding arcade. You write a short program on paper, hold it up to a camera, and the machine reads it, runs it in a sandbox, and tells you whether it solved the puzzle. Every puzzle is a path to a flag. The cabinet is a Raspberry Pi 5 with a camera and a Hailo accelerator, and the screen is a framebuffer scoreboard.

If you build on this, a mention of OCRcade is welcome.

## How a turn works

The player shows a printed puzzle card, then their code. The Pi reads the code, runs it with no network, checks the output, and shows the result on the display.

```
 IMX500 camera ─► FrameGate ─► code reader ─► CardReading ─► Exec server ─► Display
 (Pi)             (local)      (see below)    (text)         (sandbox +      (framebuffer)
                                                              validate)
```

## Reading the code

`config.yaml` lists `vlm.code_readers` in the order they are tried. One that is unreachable or errors falls through to the next. One that looks and declines is final. A model reader must be able to abstain (`readable: false`): without that, a blank frame becomes an invented program.

| Order | Reader | Where it runs |
|-------|--------|----------------|
| 1 | `litellm` (`claude-sonnet-5-5`) | Off the Pi. The URL and key come from the environment variables named in `config.yaml`. |
| 2 | `hailo_ocr` | On the Pi. PaddleOCR on the Hailo-10H. This reads the code when LiteLLM is down, slow, rate-limited, or over budget. |
| optional | `ollama` | A GPU running Qwen2.5-VL. Add it to `code_readers` to use it. Cap `vlm.ollama.num_ctx`, or the model overflows VRAM and falls back to CPU. |
| not a code reader | `hailo` Qwen2-VL-2B | Puzzle number and the camera-challenge puzzles. Its fixed input is too small for code text. |

The frame gate (blur, stillness, content) drops frames that should not be read. With the off-Pi reader unreachable, the attract screen, routing, and the camera challenges still run on the Hailo.

## Layout

```
config.yaml          endpoints and gating thresholds
vision/              capture, frame gate, ArUco markers, prompts, reader client
  backends/          litellm_vlm, hailo_ocr, ollama_vlm, hailo_vlm
game/                sandbox runner, output validators, maze, roster, RTB submitter
display/             framebuffer UI
server.py            exec and display server (:5000)
daemon.py            card → code → read → read-back → submit
puzzles/levelN/      per-puzzle data, mounted read-only at /puzzle in the sandbox
deploy/              systemd units and deploy.sh
```

Player code is untrusted. Every run goes through the sandbox: no network, a user namespace, and resource limits. The puzzle folder at `/puzzle` is the intended route to each flag.

## Setup

On the Pi the services use the system Python. `picamera2`, `hailo_platform`, and `lgpio` come from the OS image. The rest is in `requirements.txt`.

```bash
python3 server.py          # exec + display server (:5000)
python3 daemon.py          # vision loop
```

Install and update the systemd units with [`deploy/deploy.sh`](deploy/README.md). Edit `config.yaml` for the reader order, the gating thresholds, and the camera rotation. The operator guide is [`docs/SYSTEM.md`](docs/SYSTEM.md).

## License

MIT. Copyright (c) 2026 sakebomb. See [LICENSE](LICENSE).
