"""Turn a VLM's plain-text transcription into code, or an abstention.

The Hailo reader asks for bare code rather than JSON: a 2B model escaping quotes and
newlines inside a JSON string is one more way to corrupt the code.
"""
from __future__ import annotations

import re

UNREADABLE = "UNREADABLE"
_END_TOKENS = ("<|im_end|>", "<|endoftext|>")
_FENCE = re.compile(r"^```[\w+-]*\n(.*?)\n?```$", re.DOTALL)


def parse_transcription(raw: str | None) -> tuple[str | None, str]:
    """Returns (code, "") on a read, or (None, reason) when there is nothing to use."""
    if raw is None:
        return None, "no output"
    text = raw
    for token in _END_TOKENS:
        text = text.replace(token, "")
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip("\n")
    if not text.strip():
        return None, "empty output"
    if text.strip().upper() == UNREADABLE:
        return None, "model abstained"
    return text, ""
