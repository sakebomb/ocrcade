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

## 2. Score a reader

```bash
python3 tools/eval_reader.py ~/ocrcade-eval/handwriting --reader hailo-whole --run
```

Readers are defined in `evaluation/readers.py`. A new reading strategy is a new reader
there, so it is scored on the same set as everything before it.

| Reader | What it is |
|--------|-----------|
| `hailo-whole` | Qwen2-VL-2B on the Hailo-10H, whole frame at 336×336. The baseline. |
| `ollama` | The production Qwen2.5-VL-7B on a network GPU, when one is reachable. |

Each run prints a line per sample and a summary, and writes every read and score to
`<set>/results/<reader>-<timestamp>.json`.

## 3. What the numbers mean

| Metric | Meaning |
|--------|---------|
| `pass_rate` (`--run`) | Share of reads that earn the flag when run in the sandbox and validated, exactly as a real submission. **The number that matters.** |
| `exact_rate` | Read equals the truth, ignoring trailing spaces and blank edge lines (indentation counts). |
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

The Hailo model zoo's PaddleOCR line recognizer (`paddle_ocr_v6_small_recognition`, built
for the Hailo-10H) loads on kepler's HailoRT 5.1.1 and runs at 188 lines per second. It is
the next reader to build and score.
