---
name: Bug report
about: Something misbehaves (misread card, bad gate, crash, scoring error)
labels: bug
---

**What happened**
A clear description of the bug.

**Card / frame** (if a misread)
- Puzzle # and expected code:
- What the VLM returned (`readable`, `code`, `team`):
- Attach the snapshot from the log dir if possible.

**Component**
- [ ] gate (FrameGate) — fired too early/late, or on junk
- [ ] read (VLM) — wrong/hallucinated/abstained when it shouldn't
- [ ] sandbox / validator
- [ ] display / scoring / RTB

**Environment**
- Backend: ollama (model?) / hailo / degraded
- Host: kepler / other

**Logs**
```
paste relevant log lines
```
