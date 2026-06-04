# OCRcade — Player Guide

You solve a coding puzzle, then show your **code** to the arcade camera. It reads
it, runs it, and scores your team. Here's how to get read right the first time.

## Before you start

1. **Register your team name** with the organizer. The system fuzzy-matches what
   it reads to the registered list, so even a slightly-misread name still lands on
   the right team — but only if you're on the list.
2. **Put your team name in your code** as a comment:
   - Python / Bash: `# team: Megad00mer`
   - C++: `// team: Megad00mer`

   Put it as the first line. It's just a comment — it doesn't change how your code runs.

## At the station — the flow

1. **Pick the Puzzle·Lang card.** There's a printed card for each puzzle and
   language (e.g. **"PUZZLE 2 · PYTHON"**, with a black-and-white marker on it).
   Grab the one that matches what you're solving.
2. **Show that card to the camera.** The screen confirms: *"Puzzle 2 · Python —
   show your code."* (The marker is read instantly and exactly — no guessing your
   puzzle or language.)
3. **Show your code.** Put your code (laptop screen or paper) squarely in the
   camera's view. When it's sharp and still, the read fires.
4. **Check the read-back.** The screen shows the **code it read** and the **team it
   matched**. Look it over:
   - **Right?** Press the **GO button** to submit.
   - **Wrong?** (a typo, an extra `)`, a scrambled maze string, wrong team)
     Don't press GO — just **show your code again** to re-read. Re-reading is free.

## Showing code so it reads first-try (this is 90% of it)

- **Brightness up.** Dark text on a white/bright background reads best.
- **Fill the frame.** Get all your code squarely in view, facing the camera straight-on.
- **Hold steady.** A second of stillness and the read fires (a box turns green).
- **Avoid glare and angle.** Tilt the screen so room lights don't reflect in; a hard
  angle blurs small text.
- **Keep it legible.** Big monospace font. What you show is what runs — the reader
  copies your code exactly, it does **not** fix it.

## What gets misread (so check the read-back)

The camera is excellent at code but, like any reader, can slip on:
- **Long repeated runs** — maze strings like `DDRRUULL…`. Check character-by-character.
- **Stacked parentheses** `)))` — glance at the closing brackets on the last line.
- **Your team name** — the system fixes small misreads against the roster, but
  confirm it matched the right team on the read-back.

## Camera puzzles 5 & 6 (no code)

- **Puzzle 5 – Konami:** show the **Puzzle 5** card, then follow the on-camera prompts.
- **Puzzle 6 – Fruit Ninja:** show the **Puzzle 6** card, then play with the camera.
- No code and no team line needed — just show the card and play.

## Troubleshooting

| You see | Why | Fix |
|---|---|---|
| nothing happens on the puzzle card | marker not seen | fill the frame with the card, face it straight, more light |
| "show your code" but it won't read | code blurry / angled / dim | steady the screen, straighten it, raise brightness |
| read-back shows a typo | rare character misread | **don't press GO** — re-show your code |
| wrong / no team matched | team not registered, or name not in code | add `# team: NAME`, and check you're registered |
