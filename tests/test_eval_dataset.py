"""Labelled-sample manifest used by the capture and eval tools (pure, runs in CI)."""
import json
from pathlib import Path

import pytest

from evaluation.dataset import MANIFEST, Sample, append_sample, load_samples, next_name

SETS = Path(__file__).resolve().parent.parent / "evaluation" / "sets"


@pytest.mark.parametrize("label_file", sorted(SETS.glob("*.jsonl")), ids=lambda p: p.name)
def test_committed_label_files_load(tmp_path, label_file):
    (tmp_path / MANIFEST).write_text(label_file.read_text())
    samples = load_samples(tmp_path)
    assert samples and all(s.truth.strip() for s in samples)
    assert len({s.image for s in samples}) == len(samples)


def _sample(name: str = "p2-alice-001") -> Sample:
    return Sample(image=f"{name}.jpg", truth="print(1)\n", puzzle=2, lang="python",
                  writer="alice", medium="paper", blur=412.5)


def test_append_then_load_round_trips(tmp_path):
    append_sample(tmp_path, _sample("p2-alice-001"))
    append_sample(tmp_path, _sample("p2-alice-002"))
    assert load_samples(tmp_path) == [_sample("p2-alice-001"), _sample("p2-alice-002")]


def test_load_of_a_missing_manifest_is_empty(tmp_path):
    assert load_samples(tmp_path) == []


def test_load_names_the_line_of_a_bad_record(tmp_path):
    good = json.dumps(_sample().__dict__)
    (tmp_path / MANIFEST).write_text(good + "\n" + '{"image": "x.jpg"}\n')
    with pytest.raises(ValueError, match="line 2"):
        load_samples(tmp_path)


def test_next_name_counts_per_puzzle_and_writer(tmp_path):
    assert next_name(tmp_path, 2, "alice") == "p2-alice-001"
    append_sample(tmp_path, _sample("p2-alice-001"))
    append_sample(tmp_path, _sample("p3-alice-001"))
    assert next_name(tmp_path, 2, "alice") == "p2-alice-002"
    assert next_name(tmp_path, 2, "bob") == "p2-bob-001"


def test_load_rejects_an_unexpected_field(tmp_path):
    record = {**_sample().__dict__, "extra": 1}
    (tmp_path / MANIFEST).write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        load_samples(tmp_path)


def test_next_name_never_reuses_a_name_after_a_gap(tmp_path):
    append_sample(tmp_path, _sample("p2-alice-001"))
    append_sample(tmp_path, _sample("p2-alice-003"))
    assert next_name(tmp_path, 2, "alice") == "p2-alice-004"


def test_next_name_skips_an_image_already_on_disk(tmp_path):
    (tmp_path / "p2-alice-001.jpg").write_bytes(b"")
    assert next_name(tmp_path, 2, "alice") == "p2-alice-002"


@pytest.mark.parametrize("writer", ["alice/../x", "Alice", "a" * 33, ""])
def test_next_name_rejects_a_writer_that_is_not_a_plain_token(tmp_path, writer):
    with pytest.raises(ValueError):
        next_name(tmp_path, 2, writer)
