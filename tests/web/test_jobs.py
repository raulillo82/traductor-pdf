import io
import time

import pytest
from conftest import csrf

from webapp import db

PDF = b"%PDF-1.4 contenido de prueba"


@pytest.fixture
def user(app, login):
    c = app.test_client()
    login(c, "madre@example.com")
    return c


def upload(c, data=PDF, name="patrón.pdf"):
    return c.post("/api/jobs", data={"pdf": (io.BytesIO(data), name)},
                  headers={"X-CSRF-Token": csrf(c)}, content_type="multipart/form-data")


def runner(app):
    return app.extensions["runner"]


def job_row(app, job_id):
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    return row


def test_upload_creates_queued_job(app, user):
    r = upload(user)
    assert r.status_code == 202
    job = job_row(app, r.get_json()["id"])
    assert job["status"] == "queued" and job["filename"] == "patrón.pdf"
    assert runner(app).in_path(job["id"]).read_bytes() == PDF


@pytest.mark.parametrize("data,status", [(b"no soy un pdf", 400), (b"%PDF-" + b"x" * (2 * 1024 * 1024), 413)])
def test_upload_rejects_bad_files(user, data, status):
    r = upload(user, data)
    assert r.status_code == status
    assert r.get_json()["error"]


def test_upload_requires_file(user):
    r = user.post("/api/jobs", data={}, headers={"X-CSRF-Token": csrf(user)})
    assert r.status_code == 400


def test_upload_rejected_when_mordor_down(app, user, mordor):
    mordor.up = False
    app.extensions["health_cache"]["at"] = 0
    r = upload(user)
    assert r.status_code == 503 and "ordenador de Raúl" in r.get_json()["error"]
    assert user.get("/api/status").get_json()["mordor"] is False


def test_only_one_active_job_per_user(user):
    assert upload(user).status_code == 202
    assert upload(user).status_code == 409


def test_daily_limit(app, user):
    for _ in range(3):
        r = upload(user)
        runner(app).process(r.get_json()["id"])
    assert upload(user).status_code == 429


def test_successful_job_updates_progress_and_serves_pdf(app, user):
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    job = job_row(app, jid)
    assert (job["status"], job["progress"], job["pages"], job["mode"]) == ("done", 100, 3, "text")
    assert runner(app).in_path(jid).exists()  # el original se conserva (para revisar traducciones) hasta la limpieza
    view = user.get(f"/jobs/{jid}/view")
    assert view.data.startswith(b"%PDF") and view.headers["Content-Disposition"].startswith("inline")
    assert "Content-Security-Policy" not in view.headers  # no rompe el visor de PDF
    dl = user.get(f"/jobs/{jid}/download")
    assert dl.headers["Content-Disposition"].startswith("attachment")
    assert "patr" in dl.headers["Content-Disposition"] and "_es.pdf" in dl.headers["Content-Disposition"]


def test_worker_error_message_reaches_user(app, user, mordor):
    mordor.result = (3, "El PDF tiene 500 páginas y el máximo es 100.")
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    body = user.get(f"/api/jobs/{jid}").get_json()
    assert body["status"] == "error" and "500 páginas" in body["error"]
    assert user.get(f"/jobs/{jid}/view").status_code == 404


def test_generic_failure_has_friendly_message(app, user, mordor):
    mordor.result = (4, None)
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    assert user.get(f"/api/jobs/{jid}").get_json()["error"] == "No se pudo traducir el PDF."


def test_mordor_lost_mid_job(app, user, mordor):
    mordor.lost = True
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    job = job_row(app, jid)
    assert job["status"] == "error" and "conexión" in job["error"]


def test_busy_worker_is_retried(app, user, mordor, monkeypatch):
    monkeypatch.setattr("webapp.jobs.BUSY_WAIT", 0)
    calls = {"n": 0}
    real = mordor.translate

    def flaky(i, o, cb):
        calls["n"] += 1
        if calls["n"] < 3:
            return 75, "ocupado"
        return real(i, o, cb)
    mordor.translate = flaky
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    assert job_row(app, jid)["status"] == "done" and calls["n"] == 3


def test_jobs_are_private_between_users(app, user, login):
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    other = app.test_client()
    login(other, "admin@example.com")
    assert other.get(f"/api/jobs/{jid}").status_code == 404
    assert other.get(f"/jobs/{jid}/download").status_code == 404
    assert other.post(f"/api/jobs/{jid}/delete", headers={"X-CSRF-Token": csrf(other)}).status_code == 404
    assert other.get("/api/jobs").get_json()["jobs"] == []


def test_delete_job_removes_files(app, user):
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    assert user.post(f"/api/jobs/{jid}/delete", headers={"X-CSRF-Token": csrf(user)}).status_code == 200
    assert job_row(app, jid) is None and not runner(app).job_dir(jid).exists()


def test_cannot_delete_active_job(user):
    jid = upload(user).get_json()["id"]
    assert user.post(f"/api/jobs/{jid}/delete", headers={"X-CSRF-Token": csrf(user)}).status_code == 409


def test_cleanup_removes_expired_jobs_only(app, user):
    old = upload(user).get_json()["id"]
    runner(app).process(old)
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    con.execute("UPDATE jobs SET created_at=? WHERE id=?",
                (time.time() - (app.config["TRADUCTOR"]["RETENTION_HOURS"] + 1) * 3600, old))
    con.commit()
    con.close()
    new = upload(user).get_json()["id"]
    runner(app).process(new)
    runner(app).cleanup()
    assert job_row(app, old) is None and not runner(app).job_dir(old).exists()
    assert job_row(app, new) is not None


def test_recover_requeues_running_jobs(app, user):
    jid = upload(user).get_json()["id"]
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    con.execute("UPDATE jobs SET status='running', progress=40 WHERE id=?", (jid,))
    con.commit()
    con.close()
    runner(app).recover()
    assert (job_row(app, jid)["status"], job_row(app, jid)["progress"]) == ("queued", 0)


def test_recover_fails_jobs_without_input(app, user):
    jid = upload(user).get_json()["id"]
    runner(app).in_path(jid).unlink()
    runner(app).recover()
    assert job_row(app, jid)["status"] == "error"


# ---------------------------------------------------------------- push
SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}


def test_push_subscribe_validates(user):
    h = {"X-CSRF-Token": csrf(user)}
    assert user.post("/api/push/subscribe", json={"endpoint": "http://x", "keys": {}}, headers=h).status_code == 400
    assert user.post("/api/push/subscribe", json=SUB, headers=h).status_code == 200
    assert user.post("/api/push/subscribe", json=SUB, headers=h).status_code == 200  # idempotente
    assert user.get("/api/push/key").get_json()["key"]


def test_push_sent_when_job_finishes(app, user, pushed):
    user.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf(user)})
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    assert len(pushed.sent) == 1
    sent = pushed.sent[0]
    assert sent["subscription_info"]["endpoint"] == SUB["endpoint"]
    assert f"?job={jid}" in sent["data"] and sent["vapid_claims"]["sub"] == "https://example.test"


def test_push_sent_on_failure_too(app, user, pushed, mordor):
    mordor.result = (4, None)
    user.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf(user)})
    runner(app).process(upload(user).get_json()["id"])
    assert "ha fallado" in pushed.sent[0]["data"]


def test_expired_subscription_is_removed(app, user, pushed):
    from pywebpush import WebPushException

    class Gone:
        status_code = 410
    pushed.fail = WebPushException("gone", response=Gone())
    user.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf(user)})
    runner(app).process(upload(user).get_json()["id"])
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    assert con.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0] == 0
    con.close()


def test_push_network_error_keeps_subscription(app, user, pushed):
    pushed.fail = ConnectionError("sin red")
    user.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf(user)})
    runner(app).process(upload(user).get_json()["id"])
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    assert con.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0] == 1
    con.close()


def test_vapid_subject_is_accepted_by_the_real_library(app):
    """Sin simular: py_vapid rechaza un `sub` con ruta (fue un fallo real en producción)."""
    from py_vapid import Vapid, _check_sub
    sub = app.config["TRADUCTOR"]["VAPID_SUBJECT"]
    assert _check_sub(sub)
    assert not _check_sub("https://example.test/traductor")  # lo que se enviaba antes
    from webapp import push
    v = Vapid()
    v.generate_keys()
    v.sign({"sub": sub, "aud": "https://fcm.googleapis.com"})  # no lanza
    assert push.default_subject("https://x.example/traductor/") == "https://x.example"


def test_retention_default_is_a_week_and_is_shown(app, user):
    assert app.config["TRADUCTOR"]["RETENTION_HOURS"] == 168
    html = user.get("/").get_data(as_text=True)
    assert "7 días" in html and "24 horas" not in html


def test_human_retention():
    from webapp import human_retention
    assert [human_retention(h) for h in (168, 24, 48, 36)] == ["7 días", "1 día", "2 días", "36 horas"]


def test_job_younger_than_retention_survives_cleanup(app, user):
    jid = upload(user).get_json()["id"]
    runner(app).process(jid)
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    con.execute("UPDATE jobs SET created_at=? WHERE id=?", (time.time() - 6 * 24 * 3600, jid))  # 6 días
    con.commit()
    con.close()
    runner(app).cleanup()
    assert job_row(app, jid) is not None and runner(app).out_path(jid).exists()
