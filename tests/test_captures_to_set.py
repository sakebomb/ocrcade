"""Saved reads become a labelled eval set: verified reads labelled, the rest for review."""
import importlib.util
import json
import time
from pathlib import Path

import numpy as np

from evaluation.captures import load_reads, writer_token
from evaluation.dataset import load_samples
from vision.captures import CaptureLog
from vision.schema import CardReading

ROOT = Path(__file__).resolve().parents[1]
GOOD = 'print("DDRR")'
CLOCK = lambda: time.struct_time((2026, 11, 7, 21, 6, 8, 5, 311, 0))  # noqa: E731


def _tool():
    spec = importlib.util.spec_from_file_location("captures_to_set",
                                                  ROOT / "tools" / "captures_to_set.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _captures(root: Path) -> None:
    """Five reads as the daemon saves them: two submitted, one declined, a camera game."""
    log = CaptureLog(root, min_free_mb=0, clock=CLOCK)
    frame = np.random.default_rng(0).integers(0, 255, (120, 160, 3), dtype=np.uint8)

    def read(code, puzzle=3, team="Kosh", submit=False, readable=True):
        r = CardReading(readable=readable, code=code, backend="litellm",
                        reason="" if readable else "blurry")
        image = log.save(frame, r, puzzle, "python", team)
        if submit:
            log.mark_submitted(image)

    read(GOOD, submit=True)                         # earns the flag: labelled
    read('print("DDRX")', submit=True)              # submitted misread: review
    read(GOOD)                                      # good but never submitted: review
    read(None, readable=False)                      # declined: review, empty truth
    read("token", puzzle=5)                         # camera game: skipped


def test_reads_join_their_submit_marks(tmp_path):
    _captures(tmp_path)
    reads = load_reads(tmp_path)
    assert [r.submitted for r in reads] == [True, True, False, False, False]
    assert reads[0].set_image == "2026-11-07-210608-p3-1.jpg"


def test_a_verified_read_is_labelled_and_the_rest_go_to_review(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "earns_flag", lambda puzzle, lang, code: code == GOOD)
    _captures(tmp_path / "captures")
    out = tmp_path / "set"
    assert tool.build(out, tmp_path / "captures", None, "paper", run=True) == (1, 3)

    (labelled,) = load_samples(out)
    assert labelled.truth == GOOD and labelled.writer == "kosh" and labelled.puzzle == 3
    assert (out / labelled.image).exists() and labelled.blur > 0
    review = [json.loads(line) for line in (out / tool.REVIEW).read_text().splitlines()]
    assert [r["truth"] for r in review] == ['print("DDRX")', GOOD, ""]
    assert all((out / r["image"]).exists() for r in review)


def test_running_again_adds_nothing_twice(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "earns_flag", lambda *_a: True)
    _captures(tmp_path / "captures")
    out = tmp_path / "set"
    tool.build(out, tmp_path / "captures", None, "paper", run=True)
    assert tool.build(out, tmp_path / "captures", None, "paper", run=True) == (0, 0)


def test_without_the_sandbox_everything_goes_to_review(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "earns_flag", lambda *_a: True)
    _captures(tmp_path / "captures")
    assert tool.build(tmp_path / "set", tmp_path / "captures", None, "paper",
                      run=False) == (0, 4)


def test_a_puzzle_filter_keeps_only_that_puzzle(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "earns_flag", lambda *_a: True)
    _captures(tmp_path / "captures")
    assert tool.build(tmp_path / "set", tmp_path / "captures", 2, "paper", run=True) == (0, 0)


def test_earning_the_flag_means_the_sandbox_output_validates(monkeypatch):
    import game.output_validator as ov
    import game.sandbox_runner as sr
    from evaluation.captures import earns_flag

    out = {"stdout": "", "timed_out": False}
    monkeypatch.setattr(sr, "run", lambda puzzle, lang, code: out)
    monkeypatch.setattr(ov, "validate", lambda puzzle, stdout: (stdout == "ok", ""))
    out["stdout"] = "ok"
    assert earns_flag(3, "python", "code")
    assert not earns_flag(3, "python", None)                 # a declined read
    out["timed_out"] = True
    assert not earns_flag(3, "python", "code")
    out.update(stdout="no", timed_out=False)
    assert not earns_flag(3, "python", "code")


def test_writer_tokens_fit_the_sample_format():
    assert writer_token("Mega D00mer!") == "mega_d00mer"
    assert writer_token(None) == "unknown"
    assert writer_token("x" * 40) == "x" * 32
