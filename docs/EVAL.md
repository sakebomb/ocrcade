# Measuring code-reading accuracy

Every change to how OCRcade reads code is judged on the same labelled photos. This page
covers building that set on kepler, scoring a reader against it, and what the numbers mean.

## 1. Capture a labelled set (on kepler)

The capture tool shows a **reference solution** (`evaluation/solutions.py`, puzzles 0–3).
The writer copies it by hand, holds the paper square to the camera, and the operator presses
Enter. The tool grabs a short burst, keeps the sharpest frame, and labels it with the
reference text.

```bash
sudo systemctl stop ocrcade-vision                    # it owns the camera
python3 tools/capture_samples.py --writer alice --puzzle 2
python3 tools/capture_samples.py --writer alice --puzzle 3
sudo systemctl start ocrcade-vision
```

- `--writer` is a short id (`a-z`, `0-9`, `_`) so results can be split by person.
- `--truth FILE` labels with your own code instead of the reference.
- `--medium screen` for code shown on a laptop.
- A `blur` below the gate's `blur_min` is flagged: hold steadier and capture again.

Each set is a directory (default `~/ocrcade-eval/handwriting/`) of JPEGs plus
`manifest.jsonl`, one line per sample. Sets stay **out of git**: they are photos of people's
handwriting.

A useful first set: 3–5 writers × puzzles 1, 2 and 3 × 2 captures each, under the room's
real lighting. Puzzle 2 tests indentation, and puzzle 3 a 40-letter run of `U/D/L/R`.

### The real-event set: `event-2026-04`

20 frames logged at the April 2026 event, labelled by reading the full-resolution photos:
14 handwritten on paper (two writers; close, far, dark, rotated, sideways, a finger in shot)
and 6 printed on laptop screens (puzzles 1–3). The labels are in git at
`evaluation/sets/event-2026-04.jsonl`; the photos stay on kepler. To rebuild the set:

```bash
mkdir -p ~/ocrcade-eval/event-2026-04 && cd ~/ocrcade-eval/event-2026-04
cp ~/ocrcade/evaluation/sets/event-2026-04.jsonl manifest.jsonl
jq -r .image manifest.jsonl | xargs -I{} cp ~/ocrcade-logs/{} .
```

These use the old card format (a `Puzzle:`/`Team:`/`Lang:` header above the code), so the
truth is the whole page. Their code also opens files by relative path (`data.txt`), which
today's sandbox does not provide, so **on this set use `mean_cer` and `exact_rate`, not
`pass_rate`**. Frames a person could not read with certainty were left out.

## 2. Score a reader

```bash
python3 tools/eval_reader.py ~/ocrcade-eval/handwriting --reader hailo-ocr --run
```

Readers are defined in `evaluation/readers.py`. A new reading strategy is a new reader
there, so it is scored on the same set as everything before it.

| Reader | What it is |
|--------|-----------|
| `hailo-ocr` | PaddleOCR on the Hailo-10H: text detection, then each line recognized and laid back out with spaces and indentation (`vision/backends/hailo_ocr.py`). Models: `tools/fetch_hailo_ocr.sh`. |
| `hailo-whole` | Qwen2-VL-2B on the Hailo-10H, whole frame at 336×336. The baseline. |
| `ollama` | The production Qwen2.5-VL-7B on a network GPU, when one is reachable. |

Each run prints a line per sample and a summary, and writes every read and score to
`<set>/results/<reader>-<timestamp>.json`. A sample whose image or reader call fails is
recorded as an error and counted under `errors`, not as an abstention, and the run goes on.

## 3. What the numbers mean

| Metric | Meaning |
|--------|---------|
| `pass_rate` (`--run`) | Share of reads that earn the flag through the same sandbox and validator a submission uses (the server's maze-preview shortcut, already-solved check, scoring and display are skipped; none change pass/fail for puzzles 0–3). **The number that matters.** |
| `exact_rate` | Read equals the truth after normalizing: trailing spaces, Windows line endings and blank lines are ignored, and tabs count as 4 spaces. Indentation counts. |
| `mean_cer` | Character error rate: edits needed per character of the truth. 0 is perfect; 1.0 is an abstention; above 1.0 means invented text. |
| `abstain_rate` | Share of samples where the reader declined. An abstention is safe; a confident wrong read is not. |
| `latency_p50_s` / `latency_max_s` | Seconds per read. The first read includes loading the model. |

A read can pass without being exact: a misread comment or `# team:` line still runs. A read
can also be exact and fail, if the reference itself is wrong. `tests/test_eval_solutions.py`
guards against that.

## Findings so far

**2026-11-03, rendered (not handwritten) text.** `hailo-whole` abstained on all four
puzzles. Probing a single clean line cropped and padded to fill the input, the model still
invented code (it copied `# team: NAME` from the prompt). Qwen2-VL-2B on the Hailo cannot
transcribe text at any crop size, so splitting lines does not rescue it.

**2026-11-03, `event-2026-04` (real photos).** `hailo-whole`: mean CER **1.13**, exact
**0/20**, abstained **0/20**, 1.7 s per read. On real frames it never abstains: it answers
every one with invented text (a CER above 1.0 means more wrong characters than the truth
has). It is not usable as a code reader, and its confident wrong answers are the dangerous
kind.

**2026-11-03, `event-2026-04`, `hailo-ocr`.** Mean CER **0.086**, exact **0/20**,
abstained **0/20**, 1.2 s per read (1.7 s max), all on the Hailo. That is 13× fewer
character errors than `hailo-whole`. 18 of the 20 frames score below 0.14, handwritten
ones included. The remaining errors:

- Small misreads in handwriting, such as `megad100mer` and `+x+` for `txt`.
- Missing punctuation: a dropped `:` or space.
- A stray mark read as an extra line (`HM`, frame `145404`, CER 0.41).
- Two header lines merged on one slanted page (`152321`, CER 0.20).

Puzzle 2's indentation comes back right.

How the pieces were chosen, all scored on this set:

| Change | Mean CER |
|--------|----------|
| Hailo's English recognizer (97 classes), lines split into words, but crops randomly flipped | 1.65 |
| + crops kept reading left to right | 0.68 |
| + boxes grouped into lines, low-confidence (< 0.6) lines dropped, indent capped | 0.23 |
| PP-OCRv6 small recognizer, output masked to printable ASCII | 0.10 |
| PP-OCRv6 **medium** recognizer (shipped) | **0.086** |
| PP-OCRv6 small or medium detector instead of v5 mobile | 0.15 |

Each frame is read in all four rotations and the most confident one wins, which is
why a sideways page reads. That is most of the latency; reading upright first and
rotating only when that read is weak would cut it.
