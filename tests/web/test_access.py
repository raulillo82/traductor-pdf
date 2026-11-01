from conftest import csrf, make_login


def test_requires_login(client):
    assert client.get("/").headers["Location"].endswith("/login")
    assert client.get("/api/jobs").status_code == 401


def test_login_page_notes_registration_closed(client):
    html = client.get("/login").get_data(as_text=True)
    assert "Registro de nuevos usuarios desactivado" in html


def test_unknown_user_blocked_when_registration_closed(client, login):
    r = login(client, "extraño@example.com")
    assert r.headers["Location"].endswith("/login?m=closed")
    assert client.get("/").status_code == 302


def test_admin_email_is_approved_admin(client, login):
    assert login(client, "admin@example.com").headers["Location"].endswith("/")
    assert client.get("/admin").status_code == 200


def test_pre_approved_email_enters_even_if_registration_closed(client, login):
    login(client, "madre@example.com")
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 404  # no es admin


def test_unverified_google_email_rejected(client, login):
    r = login(client, "madre@example.com", verified=False)
    assert "m=unverified" in r.headers["Location"]


def test_oauth_error_goes_back_to_login(client, app):
    def boom():
        raise RuntimeError("fallo")
    app.extensions["oauth"].google.authorize_access_token = boom
    assert "m=error" in client.get("/auth/callback").headers["Location"]


def test_registration_flow_pending_then_approved_then_blocked(app, client, login, post):
    admin = app.test_client()
    login(admin, "admin@example.com")
    csrf(admin)
    admin.post("/admin/registration", data={"open": "1", "csrf": csrf(admin)})

    r = login(client, "nuevo@example.com")
    assert "m=pending" in r.headers["Location"]
    assert client.get("/").status_code == 302  # sin sesión mientras esté pendiente

    uid = _uid(app, "nuevo@example.com")
    admin.post(f"/admin/users/{uid}/approve", data={"csrf": csrf(admin)})
    login(client, "nuevo@example.com")
    assert client.get("/").status_code == 200

    admin.post(f"/admin/users/{uid}/block", data={"csrf": csrf(admin)})
    assert client.get("/").status_code == 302  # pierde el acceso al instante
    assert "m=blocked" in login(client, "nuevo@example.com").headers["Location"]


def test_closing_registration_keeps_existing_users(app, client, login):
    login(client, "madre@example.com")
    admin = app.test_client()
    login(admin, "admin@example.com")
    admin.post("/admin/registration", data={"open": "0", "csrf": csrf(admin)})
    fresh = app.test_client()
    login(fresh, "madre@example.com")
    assert fresh.get("/").status_code == 200


def test_non_admin_cannot_use_admin_actions(app, client, login):
    login(client, "madre@example.com")
    r = client.post("/admin/registration", data={"open": "1", "csrf": csrf(client)})
    assert r.status_code == 404


def test_admin_cannot_modify_admin_users(app, login):
    admin = app.test_client()
    login(admin, "admin@example.com")
    uid = _uid(app, "admin@example.com")
    assert admin.post(f"/admin/users/{uid}/block", data={"csrf": csrf(admin)}).status_code == 404


def test_post_without_csrf_is_rejected(client, login):
    login(client, "madre@example.com")
    assert client.post("/logout").status_code == 400


def test_logout_clears_session(client, login):
    login(client, "madre@example.com")
    client.post("/logout", data={"csrf": csrf(client)})
    assert client.get("/").status_code == 302


def test_session_cookie_is_scoped_to_prefix(client, login):
    make_login(client.application)(client, "madre@example.com")
    client.get("/", headers={"X-Forwarded-Prefix": "/traductor"})
    client.post("/logout", data={"csrf": csrf(client)}, headers={"X-Forwarded-Prefix": "/traductor"})
    cookie = [c for c in client.application.test_client().get(
        "/login", headers={"X-Forwarded-Prefix": "/traductor"}).headers.getlist("Set-Cookie")]
    assert any("traductor_session=" in c and "Path=/traductor" in c for c in cookie)


def test_security_headers(client):
    h = client.get("/login").headers
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY"


def _uid(app, email):
    from webapp import db
    con = db.connect(app.config["TRADUCTOR"]["DB_PATH"])
    uid = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    con.close()
    return uid
