"""Shared VLM prompt. Validated in Phase 0 against real OCRcade card photos.

Design notes (from Phase 0 failures):
- The `readable:false` escape hatch is load-bearing: on a blank/garbage frame the
  model will otherwise confidently invent a whole program. We MUST let it abstain.
- "do not fix/complete/reformat" curbs the autocomplete tendency (e.g. adding a paren).
- Asking for verbatim transcription preserves indentation, which the sandbox needs.
"""

READ_CARD_PROMPT = (
    "This image shows a coding-game card displayed on a screen: a header followed "
    "by a code body. The header has lines like 'Puzzle: <n>', 'Team: <name>', "
    "'Lang: <python|bash|cpp>'.\n"
    "If the image is blurry, empty, angled, or you cannot clearly read BOTH a header "
    'and a code body, respond with ONLY: {"readable": false, "reason": "<short why>"}.\n'
    "Otherwise transcribe what you SEE, character for character. Preserve indentation, "
    "whitespace, quotes, parentheses, and every symbol. Do NOT fix, complete, reformat, "
    "balance, or interpret the code — copy exactly what is on the screen, even if it "
    "looks wrong. Read the team name verbatim including any digits or symbols.\n"
    'Respond with ONLY this JSON: {"readable": true, "puzzle": <int>, "team": "<str>", '
    '"lang": "<python|bash|cpp>", "code": "<str>"}'
)

# Coarse prompt for the Hailo 2B (336px): it can only reliably read the large
# header number, so we ask for just that.
READ_NUMBER_PROMPT = (
    "This image shows a game card. Read ONLY the integer that appears after the word "
    'Puzzle. Respond with ONLY this JSON: {"puzzle": <int>}. If you cannot read a '
    'number, respond {"puzzle": null}.'
)
