"""State-machine tests for the capture pipeline (pure — runs in CI).

Drives Pipeline.on_frame with fake collaborators; no camera/GPU/real time."""
from dataclasses import dataclass

from vision.pipeline import Pipeline, State
from vision.schema import CardReading

ROSTER = ["Megad00mer", "Anael", "byte_me"]


def GRAB():  # fake hi-res grab
    return "M"


@dataclass
class FakeMarker:
    marker_id: int
    puzzle: int
    lang: str


class FakeMarkers:
    def __init__(self):
        self.current: FakeMarker | None = None

    def pick(self, _frame):
        return self.current


class FakeGate:
    def __init__(self):
        self.fire_next = False
        self.last = {"has_text": False, "progress": 0.0}
        self.resets = 0

    def update(self, _frame):
        fire, self.fire_next = self.fire_next, False
        return fire

    def reset(self):
        self.resets += 1


class FakeReader:
    def __init__(self, reading):
        self.reading = reading
        self.calls = 0

    def read_code(self, _frame):
        self.calls += 1
        return self.reading


class FakeUI:
    def __init__(self):
        self.calls = []

    def thumb(self, _f):
        self.calls.append(("thumb",))

    def await_card(self):
        self.calls.append(("await_card",))

    def puzzle_locked(self, puzzle, lang):
        self.calls.append(("puzzle_locked", puzzle, lang))

    def await_code(self, puzzle, lang, _prog):
        self.calls.append(("await_code", puzzle, lang))

    def readback(self, puzzle, lang, team, code):
        self.calls.append(("readback", puzzle, lang, team, code))

    def feedback(self, title, detail):
        self.calls.append(("feedback", title, detail))


class FakeConfirm:
    def __init__(self, decision="go"):
        self.decision = decision
        self.armed = 0

    def arm(self):
        self.armed += 1

    def poll(self):
        return self.decision


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Harness:
    def __init__(self, reader, confirm=None, **kw):
        self.ui = FakeUI()
        self.markers = FakeMarkers()
        self.gate = FakeGate()
        self.submits = []
        self.challenges = []
        self.clock = Clock()
        kw.setdefault("marker_stable_frames", 2)
        kw.setdefault("cooldown_s", 20.0)
        self.p = Pipeline(
            markers=self.markers, gate=self.gate, reader=reader, roster=ROSTER,
            ui=self.ui, submit=self.submits.append, confirm=confirm or FakeConfirm("go"),
            challenge=lambda pz, lg: self.challenges.append((pz, lg)),
            now=self.clock, **kw)

    def tick(self):
        return self.p.on_frame("L", GRAB)

    def select(self, marker, frames=2):
        self.markers.current = marker
        state = None
        for _ in range(frames):
            state = self.tick()
        return state

    def show_code(self):
        self.markers.current = None
        self.gate.fire_next = True
        return self.tick()


def _readable(code):
    return CardReading(readable=True, code=code, backend="fake", latency_s=0.1)


def test_happy_path_code_puzzle():
    h = Harness(FakeReader(_readable("# team: Megad00mer\nprint(1)")))
    assert h.select(FakeMarker(4, 2, "python")) is State.SELECTED
    assert h.show_code() is State.READBACK
    assert h.tick() is State.IDLE                       # confirm 'go' → submit
    assert h.submits == [{"puzzle": 2, "team": "Megad00mer", "lang": "python",
                          "code": "# team: Megad00mer\nprint(1)"}]
    assert ("readback", 2, "python", "Megad00mer", "# team: Megad00mer\nprint(1)") in h.ui.calls


def test_marker_not_yet_stable_stays_idle():
    h = Harness(FakeReader(_readable("# team: Anael\nx")))
    assert h.select(FakeMarker(4, 2, "python"), frames=1) is State.IDLE


def test_team_ocr_slip_recovered_via_roster():
    h = Harness(FakeReader(_readable("# team: Megadd0mer\nprint(1)")))  # misread
    h.select(FakeMarker(4, 2, "python"))
    h.show_code()
    h.tick()
    assert h.submits[0]["team"] == "Megad00mer"          # roster fixed the slip


def test_unreadable_stays_selected_and_feeds_back():
    h = Harness(FakeReader(CardReading.unreadable("blurry", "fake")))
    h.select(FakeMarker(4, 2, "python"))
    assert h.show_code() is State.SELECTED               # not readback
    assert h.submits == []
    assert ("feedback", "CARD NOT READ", "blurry") in h.ui.calls


def test_unregistered_team_rejected():
    h = Harness(FakeReader(_readable("# team: Nobody\nprint(1)")))
    h.select(FakeMarker(4, 2, "python"))
    assert h.show_code() is State.SELECTED
    assert h.submits == []
    assert any(c[0] == "feedback" and "TEAM" in c[1] for c in h.ui.calls)


def test_camera_challenge_routes_without_code():
    reader = FakeReader(_readable("x"))
    h = Harness(reader)
    h.markers.current = FakeMarker(13, 5, "python")       # puzzle 5 = Konami
    h.tick()
    assert h.tick() is State.SELECTED                      # locked, not started
    assert h.challenges == []
    assert h.tick() is State.IDLE                          # still held, confirm_s is 0
    assert h.challenges == [(5, "python")]
    assert h.submits == [] and reader.calls == 0           # no code read for a challenge


def test_challenge_starts_only_after_confirmation_while_the_card_is_held():
    reader = FakeReader(_readable("x"))
    h = Harness(reader, confirm_s=2.0)
    assert h.select(FakeMarker(16, 6, "python")) is State.SELECTED
    h.clock.t = 1.0
    assert h.tick() is State.SELECTED
    assert h.challenges == []
    h.clock.t = 2.0
    assert h.tick() is State.IDLE
    assert h.challenges == [(6, "python")]
    assert reader.calls == 0


def test_a_stray_frame_on_a_camera_puzzle_does_not_ask_for_code():
    reader = FakeReader(_readable("x"))
    h = Harness(reader, confirm_s=2.0)
    assert h.select(FakeMarker(16, 6, "python")) is State.SELECTED
    h.clock.t = 2.0
    h.markers.current = FakeMarker(1, 1, "python")
    assert h.tick() is State.SELECTED
    assert h.challenges == []
    assert ("await_code", 6, "python") not in h.ui.calls
    assert h.ui.calls[-1][0] == "puzzle_locked"


def test_challenge_does_not_start_if_the_card_leaves():
    reader = FakeReader(_readable("x"))
    h = Harness(reader, confirm_s=2.0)
    assert h.select(FakeMarker(16, 6, "python")) is State.SELECTED
    h.markers.current = None
    h.clock.t = 2.0
    assert h.tick() is State.IDLE
    assert h.challenges == []
    assert reader.calls == 0


def test_marker_must_be_stable_n_frames():
    h = Harness(FakeReader(_readable("# team: Anael\nx")), marker_stable_frames=3)
    assert h.select(FakeMarker(4, 2, "python"), frames=2) is State.IDLE
    assert h.tick() is State.SELECTED                      # 3rd consecutive


def test_confirmation_holds_before_ready_your_code():
    reader = FakeReader(_readable("# team: Anael\nx"))
    h = Harness(reader, confirm_s=2.0, settle_s=3.0, clear_frames=1)
    assert h.select(FakeMarker(1, 1, "python")) is State.SELECTED
    assert h.ui.calls[-1] == ("puzzle_locked", 1, "python")
    h.markers.current = None
    h.gate.fire_next = True
    h.clock.t = 1.0
    assert h.tick() is State.SELECTED
    assert reader.calls == 0
    assert not any(c[0] == "await_code" for c in h.ui.calls)
    h.clock.t = 2.0
    h.gate.fire_next = True
    assert h.tick() is State.SELECTED          # "ready" is up, settle is not done
    assert reader.calls == 0
    assert h.ui.calls[-1] == ("await_code", 1, "python")
    h.clock.t = 3.0
    h.gate.fire_next = True
    assert h.tick() is State.READBACK


def test_code_read_waits_for_the_puzzle_card_to_leave_and_settle():
    reader = FakeReader(_readable("# team: Anael\nx"))
    h = Harness(reader, settle_s=3.0, clear_frames=2)
    assert h.select(FakeMarker(4, 2, "python")) is State.SELECTED
    h.markers.current = FakeMarker(4, 2, "python")
    h.gate.fire_next = True
    h.clock.t = 5.0
    assert h.tick() is State.SELECTED
    assert reader.calls == 0
    h.markers.current = None
    h.gate.fire_next = True
    assert h.tick() is State.SELECTED          # one clear frame is not enough
    h.gate.fire_next = True
    assert h.tick() is State.READBACK


def test_one_stray_frame_does_not_replace_the_locked_puzzle():
    reader = FakeReader(_readable("# team: Anael\nx"))
    h = Harness(reader)
    h.select(FakeMarker(1, 1, "python"))
    h.markers.current = FakeMarker(16, 6, "python")       # one frame looks like Fruit Ninja
    assert h.tick() is State.SELECTED
    assert h.challenges == []
    assert (h.p._puzzle, h.p._lang) == (1, "python")
    assert h.tick() is State.SELECTED                     # second frame locks puzzle 6
    assert h.challenges == []
    assert (h.p._puzzle, h.p._lang) == (6, "python")
    assert h.tick() is State.IDLE                         # still held, so the game starts
    assert h.challenges == [(6, "python")]
    assert reader.calls == 0


def test_different_marker_reselects():
    h = Harness(FakeReader(_readable("# team: Anael\nx")))
    h.select(FakeMarker(4, 2, "python"))
    assert (h.p._puzzle, h.p._lang) == (2, "python")
    h.markers.current = FakeMarker(7, 3, "python")         # different card mid-select
    h.tick()
    assert (h.p._puzzle, h.p._lang) == (2, "python")       # one frame is not enough
    h.tick()
    assert (h.p._puzzle, h.p._lang) == (3, "python")


def test_cooldown_blocks_immediate_rescan():
    h = Harness(FakeReader(_readable("# team: Anael\nx")))
    h.select(FakeMarker(4, 2, "python"))
    h.show_code()
    h.tick()                                               # submit → cooldown
    h.markers.current = FakeMarker(7, 3, "python")
    assert h.tick() is State.IDLE                          # still cooling down
    h.clock.t = 21.0
    assert h.select(FakeMarker(7, 3, "python")) is State.SELECTED  # past cooldown


def test_task_stays_up_while_the_puzzle_card_is_held():
    reader = FakeReader(_readable("# team: Anael\nx"))
    h = Harness(reader, confirm_s=2.0, clear_frames=2)
    assert h.select(FakeMarker(4, 2, "python")) is State.SELECTED
    h.clock.t = 30.0                           # long past confirm_s, card still up
    assert h.tick() is State.SELECTED
    assert h.ui.calls[-1] == ("puzzle_locked", 2, "python")
    assert not any(c[0] == "await_code" for c in h.ui.calls)
    h.markers.current = None
    h.tick()                                   # one missed frame is not the card leaving
    assert h.ui.calls[-1] == ("puzzle_locked", 2, "python")
    h.tick()
    assert h.ui.calls[-1] == ("await_code", 2, "python")
