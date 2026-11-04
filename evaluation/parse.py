"""Turn a VLM's plain-text transcription into code, or an abstention.

The Hailo reader asks for bare code rather than JSON: a 2B model escaping quotes and
newlines inside a JSON string is one more way to corrupt the code.
"""
from __future__ import annotations

UNREADABLE = "UNREADABLE"
_END_TOKENS = ("<|im_end|>", "<|endoftext|>")


def _drop_fences(lines: list[str]) -> list[str]:
    """Remove a ``` opening line and a ``` closing line. The closing one is missing when
    the read was cut off at the token limit."""
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return lines


def parse_transcription(raw: str | None) -> tuple[str | None, str]:
    """Returns (code, "") on a read, or (None, reason) when there is nothing to use.
    Leading spaces on the first line are kept: they are indentation."""
    if raw is None:
        return None, "no output"
    text = raw
    for token in _END_TOKENS:
        text = text.replace(token, "")
    text = "\n".join(_drop_fences(text.strip("\n").rstrip().split("\n"))).strip("\n")
    if not text.strip():
        return None, "empty output"
    if text.strip().rstrip(".").upper() == UNREADABLE:
        return None, "model abstained"
    return text, ""
