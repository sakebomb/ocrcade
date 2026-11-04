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
| `litellm` | A cloud vision model through the homelab LiteLLM proxy over Tailscale (`vlm.litellm.model`). Needs `/etc/ocrcade/litellm.env`, so run the eval as root with that file loaded. Photos leave the Pi. |
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

**2026-11-03, `event-2026-04`, `hailo-ocr`.** Mean CER **0.072**, exact **0/20**,
abstained **0/20**, 1.25 s per read (1.8 s max), all on the Hailo. That is about 15×
fewer character errors than `hailo-whole`. 19 of the 20 frames score below 0.14,
handwritten ones included. The remaining errors:

- Small misreads in handwriting, such as `megad00mer` read as `megad100mer`.
- Missing punctuation: a dropped `:` or space.
- A stray mark read as an extra line (`HM`, frame `145404`).
- Two header lines merged on one slanted page (`152321`, CER 0.19, the worst frame).

Puzzle 2's indentation comes back right.

How the pieces were chosen, all scored on this set:

| Change | Mean CER |
|--------|----------|
| Hailo's English recognizer (97 classes), lines split into words, but crops randomly flipped | 1.65 |
| + crops kept reading left to right | 0.68 |
| + boxes grouped into lines, low-confidence (< 0.6) lines dropped, implausibly deep indents reset to 0 | 0.23 |
| PP-OCRv6 small recognizer, output masked to printable ASCII | 0.10 |
| PP-OCRv6 medium recognizer | 0.086 |
| + indent measured on the ink, not the merged blob; space restored where a long crop is cut (shipped) | **0.072** |
| PP-OCRv6 small or medium detector instead of v5 mobile | 0.15 |

The v6 recognizers output logits, not probabilities. Until they were put through a
softmax, the line confidence floor (0.6) never dropped anything, so the v6 rows above
were measured with no floor. With real probabilities the floor barely moves the score
(0.068–0.076 for floors 0.5–0.8).

**2026-11-03, rotation and abstention, tuned on all 479 logged event frames.** A frame
is read upright first. If that read has at least 20 characters at mean confidence 0.85
or more, the other rotations are skipped; that holds for 295 of the 479 frames. The
median read drops from 1.33 s to 0.46 s, with CER unchanged (0.073). The reader
declines a frame with fewer than 15 characters or confidence below 0.70. On the logged
frames that declines 129 of 479: empty scenes, a lone `A`, `16.4`, and some confident
garbage at 0.64. None of the 20 labelled cards are declined; the weakest reads 37
characters at 0.90.

**2026-11-03, `event-2026-04`, `litellm` with `grok-sub` (Grok 4.7).** On the 13 frames
that answered within 30 s: mean CER **0.009**, 9 of 13 exact. The other 7 timed out,
and the median read took 14 s. Grok reasons before it answers, and on one frame that
took 8.5 to 68 s across runs (650 to 4,700 reasoning tokens). `reasoning_effort: low`
did not reliably help. It reads far better than anything on the Pi, but players cannot
wait that long. It needs a non-reasoning vision model before it goes in front of
`hailo_ocr` in `vlm.code_readers`.
