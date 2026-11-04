"""/submit runs player code, so it takes the same operator token as the display and
control endpoints. The run itself is faked: these tests never start a sandbox."""
import pytest

import server

TOKEN = "test-token"
SUBMISSION = {"puzzle": 0, "team": "t", "lang": "python", "code": "print(1)"}


@pytest.fixture
def client(monkeypatch):
    started = []
    monkeypatch.setattr(server, "OPERATOR_TOKEN", TOKEN)
    monkeypatch.setattr(server, "_watchdog", lambda *a: started.append(a))
    return server.app.test_client(), started


@pytest.mark.parametrize("headers", [{}, {"X-Operator-Token": "wrong"}])
def test_submit_without_the_operator_token_is_forbidden_and_runs_nothing(client, headers):
    c, started = client
    r = c.post("/submit", json=SUBMISSION, headers=headers)
    assert r.status_code == 403
    assert started == []


def test_submit_with_the_operator_token_is_accepted(client):
    c, started = client
    r = c.post("/submit", json=SUBMISSION, headers={"X-Operator-Token": TOKEN})
    assert r.status_code == 202
    assert len(started) == 1
