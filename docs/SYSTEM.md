# OCRcade — System & Operator Guide

How the whole thing works, how to set it up, how to run an event, and how to fix
it when something's off. For the player-facing version see
[`PLAYER_GUIDE.md`](PLAYER_GUIDE.md).

---

## 1. What OCRcade is

A **coding arcade game**. A team solves a small programming puzzle, shows their
**code to a camera**, and the machine reads it, runs it in a sandbox, checks the
output, and scores them. Two of the six puzzles are camera mini-games (Konami
code, Fruit Ninja) with no code at all.

The cabinet is a Raspberry Pi 5 (**kepler**) with an AI camera and a Hailo
accelerator. `config.yaml` lists the code readers in order: grok-fast off the Pi,
then PaddleOCR on the Hailo when that reader is down.

---

## 2. How it works, end to end

```
  Player                     kepler (Pi 5)                         code reader
  ──────                     ─────────────                         ───────────
  show PUZZLE·LANG  ─▶  camera ─▶ ArUco marker decode ─▶ SELECT(puzzle, lang)
   card (ArUco)
  show CODE         ─▶  camera ─▶ gate (sharp+steady) ─▶ grok-fast, else Hailo OCR ─▶ code text
                                      │                                          │
                                  team line  ◀───────────────────────────────────┘
                                      │   "# team: NAME"  ─▶ roster fuzzy-match
                                      ▼
                                  READ-BACK on screen  ─▶  player presses GO (button)
                                      │
                                      ▼
                                  /submit ─▶ sandbox run ─▶ validate output ─▶ score
```

The design splits the job so each part does what it's good at:

| Need | Handled by | Why |
|------|-----------|-----|
| Which puzzle / language? | **ArUco marker** on a printed card | exact, organizer-controlled, robust at any angle/blur |
| Which team? | **`# team:` line** in the code, fuzzy-matched to the **roster** | a one-character misread still resolves to the right registered team |
| Read the code | **`vlm.code_readers`**: grok-fast, then Hailo OCR | the off-Pi reader goes first; the Pi still reads code when it is down |
| Catch a misread | **read-back + button** | the player confirms what was read before it's scored |
| Don't read a bad frame | **the gate** (blur + stability) | only sharp, steady frames are sent |

Why this shape: an earlier version had the camera read *everything* (puzzle, team,
and code) off a text header. Team names and stacked symbols got misread, and the
model would even invent code for a blank frame. Moving identity to a marker + a
registered roster, and adding a human confirm, removed those failure modes. (A/B
testing showed prompt-tuning alone could **not** fix the misreads — hence the
read-back.)

---

## 3. The games (puzzles)

| # | Type | What the player does |
|---|------|----------------------|
| 1 | Code | Print the flag file `/puzzle/flag1.txt`. Beginner. |
| 2 | Code | Sum the numbers, one per line, in `/puzzle/data.txt`. |
| 3 | Code | Print the moves (`U/D/L/R`) that walk the fixed maze in `/puzzle/maze_fixed.txt`. |
| 4 | Code | Walk a 15×15 maze regenerated each time the server starts or resets. |
| 5 | Camera | **Konami code** — perform ↑↑↓↓←→←→BA with on-camera gestures. No code. |
| 6 | Camera | **Fruit Ninja** — slice fruit with hand motion. No code. |

Each code puzzle has a folder `puzzles/levelN/` on kepler, mounted read-only at
`/puzzle` inside the sandbox. What it holds differs per puzzle: level 1 the flag file to
print, level 2 the input data and expected answer, level 3 the maze. The flags the
validator awards live in `flags/flagN.txt`; `puzzles/level1/flag1.txt` must match
`flags/flag1.txt`. Code runs inside
a locked-down sandbox (`bwrap`, no network, resource limits) so untrusted player
code can't harm the Pi.

Puzzles 5 & 6 are **anonymous fun rounds** — they're not attributed to a team.

---

## 4. Hardware setup

- **kepler** — Raspberry Pi 5 (8 GB) with a Hailo accelerator. Runs the game, including the on-Pi code reader.
- **Camera** — IMX500 AI camera on kepler. Players show code/cards to it. Mount it
  so it sees a well-lit area where a laptop screen or paper can be held square-on.
- **Display** — kepler's framebuffer (`/dev/fb0`) on an attached monitor shows the
  prompts, alignment thumbnail, read-back, and results.
- **Remote reader** — optional. The first entry in `vlm.code_readers` (grok-fast) runs off the Pi. Its URL and key come from the environment variables named in `config.yaml`.
- **Button** (optional but recommended) — a momentary push-button for "GO / confirm".
  See §6.

### Network
When the first code reader is off the Pi, kepler has to reach it. If that reader is down, slow, rate-limited, or over budget, Hailo OCR on the Pi reads the code. Camera challenges and puzzle selection keep working either way.

---

## 5. Software setup & deploy

The code lives in this repo and is deployed to **`kepler:~/ocrcade`**, a git checkout
on branch `deploy`. Services are systemd units:

- **`ocrcade-exec.service`** — the Flask exec/display server (`server.py`, port 5000 on
  the address in `server.host`). Runs code, validates, drives the framebuffer, talks to scoring.
- **`ocrcade-vision.service`** — the camera daemon (`daemon.py`). The state machine.

```bash
# on kepler
sudo systemctl status ocrcade-exec ocrcade-vision
sudo systemctl restart ocrcade-vision        # after a config change
journalctl -u ocrcade-vision -f              # watch it run
curl -s http://127.0.0.1:5000/health         # {"status":"ok"}
```

Runtime dependencies are already installed on kepler: OpenCV (incl. `cv2.aruco`),
`picamera2`, `requests`, `PyYAML`, `lgpio`, and the Hailo OCR models. The first
code reader (grok-fast) runs off the Pi. Hailo OCR on kepler reads the code when
that reader is down.

### Deploy (updating the code)
From a clone of this repo, run `deploy/deploy.sh [REF]` (default `main`). It pushes the
commit into kepler's checkout, restarts both services, and checks `/health`. Event data
(`teams.json`, `flag_config.json`, `.env`, `flags/`, flag files under `puzzles/`,
`konami_config.json`) is gitignored, so a deploy never touches it. The script prints the
previous commit; deploying that commit rolls back. Setup: [`deploy/README.md`](../deploy/README.md).

---

## 6. The button — wiring & configuration

The button is how a player confirms "this read is correct, submit it." Until one is
wired, the system uses **AutoConfirm** (auto-submits after showing the read-back for
a few seconds), so it works without hardware.

### Wiring (active-low, internal pull-up)
A momentary push-button between a GPIO pin and ground. No resistor needed — the Pi's
internal pull-up is enabled in software.

```
   Pi 5 40-pin header
   ┌──────────────────┐
   │ ... GPIO17 (pin 11) ●───────┐
   │     GND    (pin 9)  ●──┐     │
   └──────────────────┘     │     │
                            │   ┌─┴─┐
                            └───┤   ├───  momentary push-button
                                └───┘
   Pressed  → pin reads LOW  (0)
   Released → pin reads HIGH (1, pulled up)
```

Default pin is **GPIO17 (header pin 11)** with ground on **pin 9**. Any free GPIO
works — set it in config.

### Configuration (`config.yaml`, `button:` section)
```yaml
button:
  mode: "auto"      # "auto" = AutoConfirm (no button). Set "gpio" to use the button.
  gpio_pin: 17      # BCM GPIO number (17 = header pin 11)
  chip: 0           # gpiochip; 0 is correct for the Pi 5 main header
```
To enable the button: wire it, set `mode: gpio`, `sudo systemctl restart ocrcade-vision`.
A press is detected as a fresh HIGH→LOW edge (debounced). If the read-back times out
with no press (`readback.timeout_s`), the turn is cancelled and it returns to "show
your card."

The button uses **lgpio** (the correct GPIO library for the Pi 5's RP1 chip;
`RPi.GPIO` does not work reliably there).

---

## 7. Roster (team identity)

Teams **pre-register**; their names go in `config.yaml`. The team a player writes in
their code (`# team: NAME`) is fuzzy-matched to this list, so a one-character misread
still lands on the right team.

```yaml
roster:
  cutoff: 0.6                 # match strictness (0–1); lower = more forgiving
  names: ["Megad00mer", "Anael", "byte_me", "Team Rocket"]
  # path: "teams_roster.txt"  # OR a newline-delimited file (added to names)
```

If the roster is **empty**, the feature is treated as *off*: whatever team name is
read is accepted verbatim (so the game still works for testing). Fill it before a
real event to get misread-recovery and to reject walk-ups.

---

## 8. The puzzle cards (ArUco)

Generate the printable cards once and print them:

```bash
python3 tools/make_puzzle_cards.py --out puzzle_cards --sheet
```

This writes 18 cards — one per (puzzle, language) — each with a big ArUco marker and
a human label like **"PUZZLE 2 · PYTHON"**, plus a contact sheet. Print them, ideally
on matte stock (less glare). The marker ID → (puzzle, language) mapping is fixed in
`vision/markers.py`; you don't need to configure anything unless you re-map it.

Players show their **code** on a laptop screen or on paper — see the Player Guide.

---

## 9. Running an event (operator runbook)

**Before doors open**
1. Generate + print the 18 puzzle cards (§8). Lay them out where teams can grab them.
2. Put the registered teams in `roster.names` (§7).
3. Confirm the first code reader answers, or that Hailo OCR is loaded on the Pi.
4. Wire + enable the button if using one (§6).
5. `sudo systemctl restart ocrcade-exec ocrcade-vision`; confirm `/health` is ok and
   the daemon log says `degraded=False`.
6. Dry-run one card of each puzzle yourself under the real lighting (see §10 tuning).

**During play** — a team:
1. grabs the **Puzzle·Lang card** and shows it → screen says *"Puzzle N · Lang"*
2. shows their **code** (with `# team: NAME`) → screen shows the read-back
3. checks it and presses **GO** → it runs and scores; or re-shows to re-read.

**Watch the daemon log** (`journalctl -u ocrcade-vision -f`) — it narrates every
SELECT, read, team match, and submit.

---

## 10. Tuning for the room

Lighting and camera angle matter more than anything else.

- **Gate** (`config.yaml`, `gating:`): if good cards aren't triggering, lower
  `blur_min` (default 60); if junk frames fire, raise it. `stable_frames` is how long
  (in frames at `scan_fps`) a card must be held still. These were validated on 323
  real cards — start from the defaults.
- **Marker selection** (`select.marker_stable_frames`): how many frames the puzzle
  card must be seen to select. Raise it if cards select too eagerly. After it locks,
  the code read waits until that card has left the frame (`clear_frames`) and
  `settle_s` seconds have passed, so the puzzle card itself is not read as code.
- **Camera**: square-on, filling the frame, no glare. `camera.rotate` (0/90/180/270)
  if the camera is mounted sideways.
- **Read-back hold** (`readback.hold_s`, auto mode): how long the read shows before
  auto-submit. Irrelevant once the button is enabled.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|--------|--------------|-----|
| daemon log: `degraded=True` | the first code reader is unreachable | Hailo OCR still reads; check that reader's URL, key, and network |
| code reads come back empty | bad lighting, or every reader failed | improve the light and hold steady; confirm the Hailo OCR models are installed |
| puzzle card never selects | marker out of frame / glare | fill frame with the card; matte print; lower `select.marker_stable_frames` |
| "TEAM NOT RECOGNISED" | team not in roster / no `# team:` line | add to `roster.names`; ensure the code has the comment |
| button does nothing | `mode` still `auto`, or wiring | set `button.mode: gpio`; check pin/GND; restart |
| nothing on the monitor | exec service / framebuffer | `systemctl status ocrcade-exec`; check `/dev/fb0` |
| exec won't start | port 5000 busy / config | `journalctl -u ocrcade-exec`; ensure old copy stopped |

**Revert to the previous version:** run `deploy/deploy.sh <previous-commit>`; each
deploy prints the exact command. On kepler, `git -C ~/ocrcade log --oneline -1` shows
what is live.

---

## 12. Advice & recommendations

- **Lighting is 90% of reliability.** A diffuse light on the card area and a matte
  surface beats any software tuning. Avoid a single hard light that reflects off
  laptop screens into the camera.
- **Use the button for a real event.** AutoConfirm is fine for testing, but at an
  event the button gives players control and stops a misread from auto-scoring. A big
  arcade-style button is more fun and more obvious than a tiny tactile one.
- **Fill the roster.** It's the safety net that makes team names bulletproof; without
  it a misread name is accepted as-is.
- **The on-Pi reader is what keeps an event going.** Confirm the Hailo OCR models are installed before doors open, so a remote-reader outage still reads code.
- **Mazes and stacked parens are the misread-prone cases** — the read-back exists
  exactly for these; tell players to glance at long strings before pressing GO.
- **Keep the old version around** until a new one is proven on-device. Every deploy
  is reversible by design.
- **A down remote reader is not a down cabinet.** `degraded=True` means Hailo OCR is reading instead. The camera games keep working either way.

### Possible future improvements
- A dedicated read-back screen (currently shown via the preview view).
- Per-team ArUco cards if you ever want scored team-vs-team camera rounds.
- A reader that stays up without a remote API.
- Auto-detected camera rotation, so the mount orientation doesn't need config.
