"""VLMClient.read_code tries code readers in order: an unreachable or failing reader
falls through to the next, but a reader that looked and declined (a bad frame) is
final. Stdlib-only fakes; no camera, network or Hailo."""
from vision.schema import CardReading
from vision.vlm_client import VLMClient


class FakeReader:
    def __init__(self, name, up=True, result=None):
        self.name, self.up, self.result, self.calls = name, up, result, 0

    def available(self):
        return self.up

    def read_card(self, _img):
        raise AssertionError("not used")

    def read_code(self, _img):
        self.calls += 1
        return self.result


def ok(name):
    return CardReading(readable=True, code="print(1)", backend=name)


def test_first_available_reader_answers():
    a, b = FakeReader("a", result=ok("a")), FakeReader("b", result=ok("b"))
    r = VLMClient(code_readers=[a, b]).read_code(None)
    assert r.backend == "a" and b.calls == 0


def test_unreachable_reader_is_skipped():
    a, b = FakeReader("a", up=False), FakeReader("b", result=ok("b"))
    r = VLMClient(code_readers=[a, b]).read_code(None)
    assert r.backend == "b" and a.calls == 0


def test_failed_read_falls_through_to_the_next_reader():
    a = FakeReader("a", result=CardReading.failure("timeout", "a"))
    b = FakeReader("b", result=ok("b"))
    r = VLMClient(code_readers=[a, b]).read_code(None)
    assert r.readable and r.backend == "b"


def test_a_reader_that_declines_is_final():
    a = FakeReader("a", result=CardReading.unreadable("blurry", "a"))
    b = FakeReader("b", result=ok("b"))
    r = VLMClient(code_readers=[a, b]).read_code(None)
    assert not r.readable and r.reason == "blurry" and b.calls == 0


def test_no_reader_left_is_a_failure_naming_the_last_error():
    a = FakeReader("a", result=CardReading.failure("model not loaded", "a"))
    b = FakeReader("b", up=False)
    r = VLMClient(code_readers=[a, b]).read_code(None)
    assert not r.readable and r.failed and "model not loaded" in r.reason


def test_degraded_when_the_first_code_reader_is_down():
    assert VLMClient(code_readers=[FakeReader("a", up=False), FakeReader("b")]).degraded
    assert not VLMClient(code_readers=[FakeReader("a"), FakeReader("b", up=False)]).degraded


class FakeTextCheck:
    def __init__(self, lines):
        self.lines = lines

    def count_text_lines(self, _img):
        return self.lines


def test_sees_text_when_the_detector_finds_a_line():
    assert VLMClient(text_check=FakeTextCheck(2)).sees_text(None)


def test_no_text_when_the_detector_finds_nothing():
    assert not VLMClient(text_check=FakeTextCheck(0)).sees_text(None)


def test_unsure_detector_never_blocks_a_read():
    assert VLMClient(text_check=FakeTextCheck(None)).sees_text(None)   # Hailo down
    assert VLMClient().sees_text(None)                                # no check set


def test_config_reads_code_with_claude_only_and_keeps_the_text_check():
    # Hailo OCR's handwriting reads were garbage, and a slow Claude read fell through to
    # them and was submitted. A failed Claude read now asks the player to show it again.
    import pytest

    pytest.importorskip("cv2")
    from vision import load_config
    from vision.vlm_client import from_config

    cfg = load_config()["vlm"]
    client = from_config(cfg)
    assert [r.name for r in client.code_readers] == ["litellm"]
    assert cfg["litellm"]["timeout_s"] >= 30                   # handwriting takes 6-10 s
    assert client.text_check is not None and client.text_check.name == "hailo-ocr"


def test_a_hailo_code_reader_doubles_as_the_text_check():
    import pytest

    pytest.importorskip("cv2")
    from vision import load_config
    from vision.vlm_client import from_config

    cfg = dict(load_config()["vlm"], code_readers=["litellm", "hailo_ocr"])
    client = from_config(cfg)
    assert client.text_check is client.code_readers[1]


def test_failure_is_unreadable_and_flagged():
    r = CardReading.failure("boom", "x", 1.5)
    got = (r.readable, r.failed, r.reason, r.backend, r.latency_s)
    assert got == (False, True, "boom", "x", 1.5)
    assert not CardReading.unreadable("blurry").failed
