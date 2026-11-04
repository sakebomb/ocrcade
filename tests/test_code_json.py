"""Parsing a vision model's JSON answer to READ_CODE_PROMPT (shared by the Ollama and
LiteLLM backends). A garbled answer is a backend failure, so the client can try the
next reader; an explicit readable:false is the model declining, which is final."""
from vision.schema import code_reading_from_json


def test_readable_answer_gives_the_code():
    r = code_reading_from_json('{"readable": true, "code": "print(1)"}', "m", 2.0)
    assert (r.readable, r.code, r.backend, r.latency_s, r.failed) == (True, "print(1)", "m", 2.0,
                                                                      False)


def test_answer_wrapped_in_a_markdown_fence_is_accepted():
    raw = '```json\n{"readable": true, "code": "ls"}\n```'
    assert code_reading_from_json(raw, "m", 0).code == "ls"


def test_declining_is_final_not_a_failure():
    r = code_reading_from_json('{"readable": false, "reason": "blurry"}', "m", 0)
    assert not r.readable and not r.failed and r.reason == "blurry"


def test_garbage_is_a_failure():
    r = code_reading_from_json("I see some code about printing", "m", 0)
    assert not r.readable and r.failed and r.reason.startswith("non-JSON")


def test_readable_without_code_is_a_failure():
    r = code_reading_from_json('{"readable": true}', "m", 0)
    assert not r.readable and r.failed


def test_non_object_json_is_a_failure():
    assert code_reading_from_json('["print(1)"]', "m", 0).failed
