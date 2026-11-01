import sys
from pathlib import Path

import pytest
from flask import redirect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from webapp import create_app  # noqa: E402
from webapp.mordor import MordorError  # noqa: E402


class FakeMordor:
    def __init__(self):
        self.up = True
        self.result = (0, None)
        self.lost = False
        self.events = [("PAGES", "3"), ("MODE", "text"), ("PROGRESS", "50"), ("PROGRESS", "100")]

    def health(self):
        return self.up

    def translate(self, in_path, out_path, on_event):
        if self.lost:
            raise MordorError("caída")
        for kind, value in self.events:
            on_event(kind, value)
        if self.result[0] == 0:
            Path(out_path).write_bytes(b"%PDF-1.4 traducido")
        return self.result


class FakePush:
    def __init__(self):
        self.sent = []
        self.fail = None

    def __call__(self, **kw):
        self.sent.append(kw)
        if self.fail:
            raise self.fail


@pytest.fixture
def mordor():
    return FakeMordor()


@pytest.fixture
def pushed():
    return FakePush()


@pytest.fixture
def app(tmp_path, mordor, pushed):
    app = create_app({
        "DATA_DIR": str(tmp_path), "DB_PATH": str(tmp_path / "app.db"),
        "START_RUNNER": False, "SECURE_COOKIES": False,
        "ADMIN_EMAILS": {"admin@example.com"}, "PRE_APPROVED_EMAILS": {"madre@example.com"},
        "MAX_MB": 1, "MAX_JOBS_PER_DAY": 3, "BASE_URL": "https://example.test/traductor",
    }, mordor=mordor, webpush_fn=pushed)
    app.config["TESTING"] = True
    app.extensions["oauth"].google.authorize_redirect = lambda uri: redirect("https://accounts.google.com/x")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def make_login(app):
    """Devuelve login(client, email, verified=True) que simula la vuelta de Google."""
    def login(client, email, verified=True, name="Nombre"):
        app.extensions["oauth"].google.authorize_access_token = lambda: {
            "userinfo": {"email": email, "email_verified": verified, "name": name}}
        return client.get("/auth/callback")
    return login


@pytest.fixture
def login(app):
    return make_login(app)


def csrf(client):
    with client.session_transaction() as s:
        s.setdefault("csrf", "token-de-prueba")
        return s["csrf"]


@pytest.fixture
def post(client):
    def _post(url, **kw):
        headers = kw.pop("headers", {})
        headers["X-CSRF-Token"] = csrf(client)
        return client.post(url, headers=headers, **kw)
    return _post
