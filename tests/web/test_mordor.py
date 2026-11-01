import subprocess

import pytest

from webapp.mordor import MordorClient, MordorError


class Result:
    def __init__(self, rc=0, out="ok\n"):
        self.returncode, self.stdout = rc, out


def client():
    return MordorClient(["192.0.2.31", "192.0.2.22"], "user", "/k", "/kh")


def test_health_tries_cable_then_wifi(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[-2])
        if "192.0.2.31" in cmd[-2]:
            raise subprocess.TimeoutExpired(cmd, 1)
        return Result()
    monkeypatch.setattr(subprocess, "run", run)
    assert client().find_host() == "192.0.2.22"
    assert seen[0].endswith("192.0.2.31") and seen[1].endswith("192.0.2.22")


def test_health_false_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(255, ""))
    assert client().health() is False


def test_health_requires_exact_ok(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(0, "algo raro"))
    assert client().health() is False


def test_ssh_command_is_locked_down():
    cmd = client()._base("192.0.2.31")
    joined = " ".join(cmd)
    for needle in ("IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes",
                   "UserKnownHostsFile=/kh", "user@192.0.2.31"):
        assert needle in joined


def test_translate_raises_when_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(255, ""))
    (tmp_path / "in.pdf").write_bytes(b"%PDF-")
    with pytest.raises(MordorError):
        client().translate(tmp_path / "in.pdf", tmp_path / "out.pdf", lambda *a: None)
