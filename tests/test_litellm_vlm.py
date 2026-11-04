"""LiteLLM backend request and failure handling, with requests faked (no network).
Needs cv2 + numpy + requests, so it skips in the lightweight CI job."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
requests = pytest.importorskip("requests")

from vision.backends import litellm_vlm  # noqa: E402
from vision.backends.litellm_vlm import LiteLLMVLMBackend  # noqa: E402

FRAME = np.zeros((3000, 2000, 3), np.uint8)


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self.ok, self._p, self.text = status, status < 400, payload, text

    def json(self):
        return self._p


def answer(content):
    return Resp(payload={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "http://llm:4000/")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    return LiteLLMVLMBackend(model="grok-sub")


def test_sends_prompt_and_downscaled_image_with_bearer_key(backend, monkeypatch):
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, body=json, headers=headers)
        return answer('{"readable": true, "code": "ls"}')

    monkeypatch.setattr(litellm_vlm.requests, "post", post)
    r = backend.read_code(FRAME)
    assert r.readable and r.code == "ls"
    assert seen["url"] == "http://llm:4000/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer sk-test"}
    parts = seen["body"]["messages"][0]["content"]
    assert seen["body"]["model"] == "grok-sub" and parts[0]["type"] == "text"
    uri = parts[1]["image_url"]["url"]
    assert uri.startswith("data:image/jpeg;base64,")
    import base64

    import cv2
    img = cv2.imdecode(np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8), 1)
    assert max(img.shape[:2]) == litellm_vlm.MAX_SIDE


def test_refused_request_is_a_failure_so_the_next_reader_runs(backend, monkeypatch):
    monkeypatch.setattr(litellm_vlm.requests, "post",
                        lambda *a, **k: Resp(429, text="budget exceeded"))
    r = backend.read_code(FRAME)
    assert r.failed and "429" in r.reason


def test_timeout_is_a_failure_that_does_not_echo_the_key(backend, monkeypatch):
    def post(*a, **k):
        raise requests.Timeout("read timed out (Authorization: Bearer sk-test)")

    monkeypatch.setattr(litellm_vlm.requests, "post", post)
    r = backend.read_code(FRAME)
    assert r.failed and "sk-test" not in r.reason


def test_model_declining_is_final(backend, monkeypatch):
    monkeypatch.setattr(litellm_vlm.requests, "post",
                        lambda *a, **k: answer('{"readable": false, "reason": "blurry"}'))
    r = backend.read_code(FRAME)
    assert not r.readable and not r.failed


def test_unconfigured_backend_is_unavailable_without_a_network_call(monkeypatch):
    monkeypatch.delenv("LITELLM_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("no request expected")

    monkeypatch.setattr(litellm_vlm.requests, "get", boom)
    assert not LiteLLMVLMBackend(model="grok-sub").available()


def test_available_only_when_the_key_can_use_the_model(backend, monkeypatch):
    monkeypatch.setattr(litellm_vlm.requests, "get",
                        lambda *a, **k: Resp(payload={"data": [{"id": "grok"}]}))
    assert not backend.available()
