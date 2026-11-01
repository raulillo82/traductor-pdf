"""Web del traductor: login con Google + aprobación, cola de trabajos y aviso por push."""
import os
import re
import secrets
import time
import uuid
from functools import wraps
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from flask import (Flask, abort, g, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, push
from .jobs import JobRunner
from .mordor import MordorClient
from .strings import t

HEALTH_TTL = 10  # segundos que se cachea el estado de mordor


class PrefixedSessionInterface(SecureCookieSessionInterface):
    """La cookie de sesión solo viaja bajo el prefijo de esta app (p. ej. /traductor)."""

    def get_cookie_path(self, app):
        return request.script_root or "/"


def _read_secret(env, default=""):
    """Lee NOMBRE_FILE (un fichero con el valor) o NOMBRE directamente."""
    path = os.environ.get(env + "_FILE")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return default
    return os.environ.get(env, default)


def human_retention(hours):
    """168 -> '7 días', 24 -> '1 día', 36 -> '36 horas'."""
    if hours % 24 == 0:
        d = hours // 24
        return f"{d} día" + ("s" if d != 1 else "")
    return f"{hours} horas"


def _emails(env):
    return {e.strip().lower() for e in os.environ.get(env, "").split(",") if e.strip()}


def load_config():
    data_dir = os.environ.get("DATA_DIR", "/data")
    return {
        "DATA_DIR": data_dir,
        "DB_PATH": os.path.join(data_dir, "app.db"),
        "BASE_URL": os.environ.get("BASE_URL", "http://localhost:5002"),
        "VAPID_SUBJECT": os.environ.get("VAPID_SUBJECT", ""),
        "GOOGLE_CLIENT_ID": _read_secret("GOOGLE_CLIENT_ID"),
        "GOOGLE_CLIENT_SECRET": _read_secret("GOOGLE_CLIENT_SECRET"),
        "ADMIN_EMAILS": _emails("ADMIN_EMAILS"),
        "PRE_APPROVED_EMAILS": _emails("PRE_APPROVED_EMAILS"),
        "MAX_MB": int(os.environ.get("MAX_MB", 100)),
        "MAX_JOBS_PER_DAY": int(os.environ.get("MAX_JOBS_PER_DAY", 20)),
        "RETENTION_HOURS": int(os.environ.get("RETENTION_HOURS", 168)),
        "MORDOR_HOSTS": [h for h in os.environ.get("MORDOR_HOSTS", "").split(",") if h],
        "MORDOR_USER": os.environ.get("MORDOR_USER", "traductor"),
        "MORDOR_KEY": os.environ.get("MORDOR_KEY", "/secrets/traductor_mordor"),
        "MORDOR_KNOWN_HOSTS": os.environ.get("MORDOR_KNOWN_HOSTS", "/secrets/known_hosts"),
        "SECURE_COOKIES": os.environ.get("SECURE_COOKIES", "1") == "1",
        "START_RUNNER": True,
    }


def create_app(overrides=None, mordor=None, webpush_fn=None):
    cfg = load_config()
    cfg.update(overrides or {})
    cfg["VAPID_SUBJECT"] = cfg["VAPID_SUBJECT"] or push.default_subject(cfg["BASE_URL"])
    Path(cfg["DATA_DIR"], "jobs").mkdir(parents=True, exist_ok=True)
    db.init(cfg["DB_PATH"])

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_session_key(cfg["DATA_DIR"]),
        SESSION_COOKIE_NAME="traductor_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg["SECURE_COOKIES"],
        PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
        MAX_CONTENT_LENGTH=(cfg["MAX_MB"] + 1) * 1024 * 1024,
        TRADUCTOR=cfg,
    )
    # La app solo escucha en 127.0.0.1 detrás de nginx, que fija estas cabeceras.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1)
    app.session_interface = PrefixedSessionInterface()

    vapid_pem = Path(cfg["DATA_DIR"]) / "vapid_private.pem"
    app.config["VAPID_PUBLIC"] = push.ensure_vapid(vapid_pem)

    if mordor is None:
        mordor = MordorClient(cfg["MORDOR_HOSTS"], cfg["MORDOR_USER"], cfg["MORDOR_KEY"],
                              cfg["MORDOR_KNOWN_HOSTS"])
    runner = JobRunner(cfg, mordor, webpush_fn)
    app.extensions["runner"] = runner
    health_cache = {"at": 0.0, "ok": False}
    app.extensions["health_cache"] = health_cache

    oauth = OAuth(app)
    oauth.register("google", client_id=cfg["GOOGLE_CLIENT_ID"], client_secret=cfg["GOOGLE_CLIENT_SECRET"],
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile"})
    app.extensions["oauth"] = oauth

    # ------------------------------------------------------------ utilidades
    def conn():
        if "db" not in g:
            g.db = db.connect(cfg["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        con = g.pop("db", None)
        if con is not None:
            con.close()

    def mordor_up():
        if time.time() - health_cache["at"] > HEALTH_TTL:
            health_cache.update(at=time.time(), ok=bool(mordor.health()))
        return health_cache["ok"]

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        u = conn().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return u if u and u["status"] == "approved" else None

    def login_required(f):
        @wraps(f)
        def wrapper(*a, **kw):
            user = current_user()
            if user is None:
                session.pop("uid", None)
                if request.path.startswith("/api/"):
                    return jsonify(error="No autenticado"), 401
                return redirect(url_for("login"))
            g.user = user
            return f(*a, **kw)
        return wrapper

    def admin_required(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **kw):
            if not g.user["is_admin"]:
                abort(404)
            return f(*a, **kw)
        return wrapper

    def registration_open():
        return db.get_setting(conn(), "registration_open", "0") == "1"

    @app.before_request
    def _csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                return jsonify(error="CSRF"), 400

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if resp.mimetype != "application/pdf":  # una CSP restrictiva rompe el visor de PDF de Chrome
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return resp

    @app.context_processor
    def _ctx():
        return {"t": t, "csrf_token": csrf_token}

    @app.errorhandler(413)
    def _too_big(_e):
        return jsonify(error=t("err.too_big", max_mb=cfg["MAX_MB"])), 413

    # ------------------------------------------------------------ acceso
    def resolve_login(email, name):
        """('ok', user) o (motivo, None). Crea usuarios nuevos según las reglas de registro."""
        con = conn()
        email = email.lower()
        is_admin = email in cfg["ADMIN_EMAILS"]
        u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u is None:
            if is_admin or email in cfg["PRE_APPROVED_EMAILS"]:
                status = "approved"
            elif registration_open():
                status = "pending"
            else:
                return "closed", None
            con.execute("INSERT INTO users(email, name, status, is_admin, created_at) VALUES(?,?,?,?,?)",
                        (email, name, status, int(is_admin), db.now()))
            con.commit()
            u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        elif is_admin and (u["status"] != "approved" or not u["is_admin"]):
            con.execute("UPDATE users SET status='approved', is_admin=1 WHERE id=?", (u["id"],))
            con.commit()
            u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
        if u["status"] == "approved":
            con.execute("UPDATE users SET last_login=?, name=? WHERE id=?", (db.now(), name or u["name"], u["id"]))
            con.commit()
            return "ok", u
        return u["status"], None

    @app.get("/login")
    def login():
        if current_user():
            return redirect(url_for("index"))
        code = request.args.get("m", "")
        return render_template("login.html", message=t(f"msg.{code}") if code in
                               ("pending", "blocked", "closed", "error", "unverified") else None,
                               closed=not registration_open())

    @app.get("/auth/google")
    def auth_google():
        return oauth.google.authorize_redirect(url_for("auth_callback", _external=True))

    @app.get("/auth/callback")
    def auth_callback():
        try:
            token = oauth.google.authorize_access_token()
            info = token.get("userinfo") or {}
        except Exception as e:
            # Sin el detalle de la respuesta de Google (puede incluir códigos): solo el tipo y el motivo
            app.logger.warning("OAuth: callback falló: %s: %s", type(e).__name__, getattr(e, "error", None) or e)
            return redirect(url_for("login", m="error"))
        if not info.get("email") or not info.get("email_verified"):
            return redirect(url_for("login", m="unverified"))
        reason, user = resolve_login(info["email"], info.get("name", ""))
        if user is None:
            return redirect(url_for("login", m={"pending": "pending", "blocked": "blocked"}.get(reason, "closed")))
        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        return redirect(url_for("index"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="text/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ------------------------------------------------------------ páginas
    @app.get("/")
    @login_required
    def index():
        return render_template("index.html", user=g.user, max_mb=cfg["MAX_MB"],
                               retention=human_retention(cfg["RETENTION_HOURS"]))

    @app.get("/admin")
    @admin_required
    def admin():
        users = conn().execute(
            "SELECT u.*, (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id) AS njobs "
            "FROM users u ORDER BY (u.status='pending') DESC, u.created_at").fetchall()
        return render_template("admin.html", users=users, registration=registration_open(), user=g.user)

    @app.post("/admin/registration")
    @admin_required
    def admin_registration():
        db.set_setting(conn(), "registration_open", "1" if request.form.get("open") == "1" else "0")
        return redirect(url_for("admin"))

    @app.post("/admin/users/<int:uid>/<action>")
    @admin_required
    def admin_user(uid, action):
        con = conn()
        u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u is None or u["is_admin"] or action not in ("approve", "block", "delete"):
            abort(404)
        if action == "delete":
            con.execute("DELETE FROM users WHERE id=?", (uid,))
        else:
            con.execute("UPDATE users SET status=? WHERE id=?",
                        ("approved" if action == "approve" else "blocked", uid))
        con.commit()
        return redirect(url_for("admin"))

    # ------------------------------------------------------------ API
    def job_dict(row):
        return {"id": row["id"], "filename": row["filename"], "status": row["status"],
                "progress": row["progress"], "pages": row["pages"], "mode": row["mode"],
                "error": row["error"], "created_at": row["created_at"]}

    def own_job(job_id):
        row = conn().execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()
        if row is None:
            abort(404)
        return row

    @app.get("/api/status")
    @login_required
    def api_status():
        return jsonify(mordor=mordor_up(), max_mb=cfg["MAX_MB"],
                       message=t("status.up") if mordor_up() else t("status.down"))

    @app.get("/api/jobs")
    @login_required
    def api_jobs():
        rows = conn().execute("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
                              (g.user["id"],)).fetchall()
        return jsonify(jobs=[job_dict(r) for r in rows])

    @app.get("/api/jobs/<job_id>")
    @login_required
    def api_job(job_id):
        return jsonify(job_dict(own_job(job_id)))

    @app.post("/api/jobs")
    @login_required
    def api_create_job():
        con = conn()
        f = request.files.get("pdf")
        if f is None or not f.filename:
            return jsonify(error=t("err.no_file")), 400
        if con.execute("SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running')",
                       (g.user["id"],)).fetchone():
            return jsonify(error=t("err.busy")), 409
        today = db.now() - 24 * 3600
        n = con.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND created_at>?", (g.user["id"], today)).fetchone()[0]
        if n >= cfg["MAX_JOBS_PER_DAY"]:
            return jsonify(error=t("err.daily", n=cfg["MAX_JOBS_PER_DAY"])), 429
        if not mordor_up():
            return jsonify(error=t("err.down")), 503
        head = f.stream.read(5)
        f.stream.seek(0)
        if head != b"%PDF-":
            return jsonify(error=t("err.not_pdf")), 400
        job_id = uuid.uuid4().hex
        d = runner.job_dir(job_id)
        d.mkdir(parents=True)
        f.save(runner.in_path(job_id))
        name = re.sub(r"[\x00-\x1f\\/\"]", "_", os.path.basename(f.filename))[:200] or "documento.pdf"
        con.execute("INSERT INTO jobs(id, user_id, filename, size, created_at) VALUES(?,?,?,?,?)",
                    (job_id, g.user["id"], name, runner.in_path(job_id).stat().st_size, db.now()))
        con.commit()
        runner.submit(job_id)
        return jsonify(id=job_id), 202

    def _serve(job_id, attachment):
        job = own_job(job_id)
        out = runner.out_path(job_id)
        if job["status"] != "done" or not out.exists():
            abort(404)
        stem = re.sub(r"\.pdf$", "", job["filename"], flags=re.I)
        return send_file(out, mimetype="application/pdf", as_attachment=attachment,
                         download_name=f"{stem}_es.pdf", max_age=0)

    @app.get("/jobs/<job_id>/view")
    @login_required
    def job_view(job_id):
        return _serve(job_id, False)

    @app.get("/jobs/<job_id>/download")
    @login_required
    def job_download(job_id):
        return _serve(job_id, True)

    @app.post("/api/jobs/<job_id>/delete")
    @login_required
    def api_job_delete(job_id):
        job = own_job(job_id)
        if job["status"] in ("queued", "running"):
            return jsonify(error=t("err.busy")), 409
        import shutil
        shutil.rmtree(runner.job_dir(job_id), ignore_errors=True)
        conn().execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn().commit()
        return jsonify(ok=True)

    @app.get("/api/push/key")
    @login_required
    def api_push_key():
        return jsonify(key=app.config["VAPID_PUBLIC"])

    @app.post("/api/push/subscribe")
    @login_required
    def api_push_subscribe():
        data = request.get_json(silent=True) or {}
        keys = data.get("keys") or {}
        endpoint, p256dh, auth = data.get("endpoint"), keys.get("p256dh"), keys.get("auth")
        if not (isinstance(endpoint, str) and endpoint.startswith("https://") and p256dh and auth):
            return jsonify(error="Suscripción inválida"), 400
        con = conn()
        con.execute("INSERT INTO push_subs(user_id, endpoint, p256dh, auth, created_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth",
                    (g.user["id"], endpoint, p256dh, auth, db.now()))
        con.commit()
        return jsonify(ok=True)

    if cfg["START_RUNNER"]:
        runner.start()
    return app


def _session_key(data_dir):
    p = Path(data_dir) / "secret_key"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
        p.chmod(0o600)
    return p.read_bytes()
