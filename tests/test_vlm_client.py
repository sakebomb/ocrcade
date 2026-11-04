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


def test_failure_is_unreadable_and_flagged():
    r = CardReading.failure("boom", "x", 1.5)
    got = (r.readable, r.failed, r.reason, r.backend, r.latency_s)
    assert got == (False, True, "boom", "x", 1.5)
    assert not CardReading.unreadable("blurry").failed
