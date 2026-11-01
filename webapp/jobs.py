"""Cola de trabajos: un hilo procesa de uno en uno y llama a mordor por SSH."""
import logging
import queue
import shutil
import threading
import time
from pathlib import Path

from . import db, push
from .mordor import MordorError
from .strings import t

log = logging.getLogger(__name__)
BUSY_RETRIES = 60
BUSY_WAIT = 5


class JobRunner:
    def __init__(self, cfg, mordor, webpush_fn=None):
        self.cfg = cfg
        self.mordor = mordor
        self.webpush_fn = webpush_fn
        self.q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._last_cleanup = 0.0

    # --- rutas
    def job_dir(self, job_id):
        return Path(self.cfg["DATA_DIR"]) / "jobs" / job_id

    def in_path(self, job_id):
        return self.job_dir(job_id) / "in.pdf"

    def out_path(self, job_id):
        return self.job_dir(job_id) / "out.pdf"

    # --- ciclo de vida
    def start(self):
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def submit(self, job_id):
        self.q.put(job_id)

    def recover(self):
        """Tras un reinicio: lo que estaba en marcha vuelve a la cola si su PDF sigue ahí."""
        con = db.connect(self.cfg["DB_PATH"])
        rows = con.execute("SELECT id, status FROM jobs WHERE status IN ('queued','running') "
                           "ORDER BY created_at").fetchall()
        for r in rows:
            if self.in_path(r["id"]).exists():
                con.execute("UPDATE jobs SET status='queued', progress=0 WHERE id=?", (r["id"],))
                self.submit(r["id"])
            else:
                con.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
                            (t("job.lost"), db.now(), r["id"]))
        con.commit()
        con.close()

    def _loop(self):
        while not self._stop.is_set():
            try:
                job_id = self.q.get(timeout=30)
            except queue.Empty:
                job_id = None
            try:
                if job_id:
                    self.process(job_id)
                if time.time() - self._last_cleanup > 600:
                    self.cleanup()
            except Exception:
                log.exception("error en el hilo de trabajos")

    # --- trabajo
    def process(self, job_id):
        con = db.connect(self.cfg["DB_PATH"])
        try:
            job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "queued":
                return
            con.execute("UPDATE jobs SET status='running', started_at=?, progress=0 WHERE id=?",
                        (db.now(), job_id))
            con.commit()

            def on_event(kind, value):
                if kind == "PAGES":
                    con.execute("UPDATE jobs SET pages=? WHERE id=?", (int(value), job_id))
                elif kind == "MODE":
                    con.execute("UPDATE jobs SET mode=? WHERE id=?", (value, job_id))
                elif kind == "PROGRESS":
                    con.execute("UPDATE jobs SET progress=? WHERE id=?", (int(value), job_id))
                else:
                    if kind == "LOG":
                        log.info("worker[%s]: %s", job_id, value)
                    return
                con.commit()

            ok, error = self._run(job_id, on_event)
            if ok:
                con.execute("UPDATE jobs SET status='done', progress=100, finished_at=? WHERE id=?",
                            (db.now(), job_id))
            else:
                con.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
                            (error, db.now(), job_id))
                self.out_path(job_id).unlink(missing_ok=True)
            con.commit()
            self._notify(con, job_id, ok, error)
        finally:
            con.close()

    def _run(self, job_id, on_event):
        for _ in range(BUSY_RETRIES):
            try:
                code, err = self.mordor.translate(self.in_path(job_id), self.out_path(job_id), on_event)
            except MordorError:
                return False, t("job.lost")
            if code != 75:
                break
            time.sleep(BUSY_WAIT)
        else:
            return False, t("err.down")
        if code == 0 and self.out_path(job_id).exists() and self.out_path(job_id).stat().st_size > 0:
            return True, None
        return False, err or t("job.failed")

    def _notify(self, con, job_id, ok, error):
        job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        base = self.cfg["BASE_URL"].rstrip("/")
        payload = {"title": t("push.title") if ok else t("push.title_err"),
                   "body": t("push.body", name=job["filename"]) if ok else (error or ""),
                   "url": f"{base}/?job={job_id}"}
        subs = con.execute("SELECT * FROM push_subs WHERE user_id=?", (job["user_id"],)).fetchall()
        for s in subs:
            alive = push.send(s, payload, Path(self.cfg["DATA_DIR"]) / "vapid_private.pem",
                              self.cfg["VAPID_SUBJECT"], self.webpush_fn)
            if not alive:
                con.execute("DELETE FROM push_subs WHERE id=?", (s["id"],))
        con.commit()

    def cleanup(self):
        """Borra trabajos y ficheros pasada la retención."""
        self._last_cleanup = time.time()
        limit = db.now() - self.cfg["RETENTION_HOURS"] * 3600
        con = db.connect(self.cfg["DB_PATH"])
        try:
            old = con.execute("SELECT id FROM jobs WHERE created_at < ? AND status IN ('done','error')",
                              (limit,)).fetchall()
            for r in old:
                shutil.rmtree(self.job_dir(r["id"]), ignore_errors=True)
                con.execute("DELETE FROM jobs WHERE id=?", (r["id"],))
            con.commit()
        finally:
            con.close()
