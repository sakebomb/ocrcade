# OCRcade — How to Make a Card the Camera Reads First-Try

You show **code on a screen** to the arcade camera. It reads your code, runs it,
and scores your team. This guide gets you read correctly on the first try.

## 1. Make your card with the generator

Open **`tools/card_generator.html`** in any browser (works offline — just copy the
`tools/` folder). Fill in:

- **Puzzle** — which challenge you're solving (1–6)
- **Team** — your team name, exactly how you want it recorded
- **Language** — `python`, `bash`, or `cpp`
- **Code** — paste your solution (indentation is preserved)

Hit **Render card →**, then **Fullscreen**, and point the screen at the camera.

> The card puts a **QR code** in the top-right corner. That QR carries your
> puzzle number, team name, and language — so the reader never has to *guess*
> them. No more "Megad00mer" turning into "Megadd0mer." You only need the camera
> to read the **code**, which it's very good at.

## 2. Show it well (this is 90% of getting read first-try)

- **Brightness up.** Full screen brightness; the card is dark text on white.
- **Fill the frame.** Get the whole card — QR *and* code — squarely in view.
- **Hold steady.** When the card is sharp and still, an on-screen box turns green
  and the read fires. A second of stillness is all it takes.
- **Avoid glare and angle.** Tilt the screen so room lights don't reflect into the
  camera. Face the camera straight-on — a hard angle blurs the small text.
- **Don't crop the QR.** If the QR is cut off or glared out, the reader won't know
  which puzzle/team you are.

## 3. Check the read-back, then press GO

After it reads your card, the display shows **the exact code it read**. Look at it:

- **Looks right?** Press the **GO button** to submit.
- **Looks wrong?** (a missing character, an extra `)`, a scrambled maze string)
  Don't submit — just **show your card again** to re-read. Re-reading is free.

This step exists because the camera, like any reader, can occasionally misread a
character — especially **long runs** (like maze strings `DDRRUULL…`) or **stacked
parentheses** `)))`. The read-back lets you catch it before it costs you a try.

## 4. Tips that make code read cleanly

- **Keep it legible.** Big monospace (the generator does this). What you show is
  what runs — the reader copies it *exactly*, it does not "fix" your code.
- **Mazes / long strings:** double-check the read-back carefully — these are the
  easiest to misread by a character.
- **Deeply nested calls:** glance at the closing `)))` in the read-back.
- **Whitespace matters** for Python — the generator preserves your indentation, so
  indent in the box exactly as your solution needs.

## 5. Camera puzzles (5 & 6) — no code

- **Puzzle 5 – Konami:** show the puzzle-5 card to start, then do the Konami code
  with the on-camera prompts. Leave the code box blank in the generator.
- **Puzzle 6 – Fruit Ninja:** show the puzzle-6 card, then play with the camera.

## Troubleshooting — "CARD NOT READ"

| You see | Why | Fix |
|---|---|---|
| "Blurry" / "angled" | camera couldn't read the code sharply | steady the screen, face it straight, raise brightness |
| nothing happens | no QR detected | make sure the QR (top-right) is fully in frame and not glared |
| wrong team/puzzle | (shouldn't happen — QR is exact) | re-render the card; check you picked the right puzzle |
| read-back has a typo | rare character misread | **don't press GO** — re-show the card to re-read |
