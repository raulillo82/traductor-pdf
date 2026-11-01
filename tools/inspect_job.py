#!/usr/bin/env python3
"""Revisar una traducción: texto original vs. traducido de una página, para mejorar el glosario.

Se ejecuta en la máquina del worker (el worker). Lee la base de datos y los ficheros del servidor web
por SSH (solo lectura) y vuelve a traducir la página en el contenedor del worker, sin red.

    tools/inspect_job.py --list                     # trabajos recientes
    tools/inspect_job.py <id|parte del nombre> 12   # pares original → traducción de la página 12
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB_HOST = os.environ.get("TRADUCTOR_WEB_HOST", "")
if not WEB_HOST:
    sys.exit("Define TRADUCTOR_WEB_HOST con el host SSH del servidor web.")
WEB_DIR = os.environ.get("TRADUCTOR_WEB_DIR", "~/traductor")
IMAGE = os.environ.get("TRADUCTOR_IMAGE", "localhost/traductor-worker:latest")
GLOSS = Path(os.environ.get("TRADUCTOR_GLOSSARY", Path.home() / ".config/traductor/glosario.txt"))

QUERY = """
import sqlite3, sys, json
import os
c = sqlite3.connect("file:" + os.path.expanduser("@@DIR@@") + "/data/app.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
rows = c.execute("SELECT j.id, u.email, j.filename, j.status, j.pages, j.mode, j.created_at "
                 "FROM jobs j JOIN users u ON u.id = j.user_id ORDER BY j.created_at DESC LIMIT 40").fetchall()
print(json.dumps([dict(r) for r in rows]))
"""


def ssh(cmd, **kw):
    return subprocess.run(["ssh", WEB_HOST, cmd], capture_output=True, text=True, **kw)


def list_jobs():
    r = subprocess.run(["ssh", WEB_HOST, "python3", "-"], input=QUERY.replace("@@DIR@@", WEB_DIR),
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"No se pudo leer la base de datos: {r.stderr.strip()}")
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", nargs="?", help="id (o prefijo) o parte del nombre del fichero")
    ap.add_argument("page", nargs="?", type=int)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    jobs = list_jobs()
    if a.list or not a.job:
        import time
        for j in jobs:
            when = time.strftime("%d/%m %H:%M", time.localtime(j["created_at"]))
            print(f'{j["id"][:8]}  {when}  {j["status"]:<7} {str(j["pages"] or "?"):>3} págs  {j["mode"] or "-":<5} {j["filename"]}')
        return
    if not a.page:
        sys.exit("Indica el número de página.")

    q = a.job.lower()
    found = [j for j in jobs if j["id"].startswith(q) or q in j["filename"].lower()]
    if not found:
        sys.exit("No hay ningún trabajo que coincida (usa --list).")
    job = found[0]
    if len(found) > 1:
        print(f"Varios coinciden; uso el más reciente: {job['id'][:8]} {job['filename']}", file=sys.stderr)

    tmp = Path(tempfile.mkdtemp(prefix="inspect_"))
    try:
        src = f"{WEB_HOST}:{WEB_DIR}/data/jobs/{job['id']}/in.pdf"
        if subprocess.run(["scp", "-q", src, str(tmp / "in.pdf")]).returncode:
            sys.exit("No existe el PDF original de ese trabajo (¿ya se borró o es anterior a que se guardase?).")
        tmp.chmod(0o777)
        script = "translate_pdf_text.py" if job["mode"] == "text" else "translate_pdf.py"
        gloss_args, mounts = ["--glossary", "/app/glosario_ganchillo_es.txt"], []
        if GLOSS.is_file():
            mounts = ["-v", f"{GLOSS}:/app/glosario_externo.txt:ro,Z"]
            gloss_args = ["--glossary", "/app/glosario_externo.txt"]
        extra = ["--dump-json", "/w/pairs.json"] if job["mode"] == "text" else []
        r = subprocess.run(["podman", "run", "--rm", "--network", "none", "--userns", "keep-id:uid=1000,gid=1000",
                            "-e", "HOME=/home/app", "-e", "XDG_CACHE_HOME=/tmp", "--tmpfs", "/tmp",
                            "-v", f"{tmp}:/w:Z", *mounts, "--entrypoint", "python", IMAGE,
                            f"/app/{script}", "/w/in.pdf", "-o", "/w/o.pdf", "--pages", str(a.page), *gloss_args, *extra],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"Falló la traducción de la página:\n{r.stderr[-800:]}")
        if job["mode"] == "text":
            pairs = json.loads((tmp / "pairs.json").read_text(encoding="utf-8"))
        else:
            data = json.loads((tmp / "o_work" / "translations.json").read_text(encoding="utf-8"))
            pairs = data.get(str(a.page), [])
        print(f"{job['filename']} · página {a.page} · {job['mode']} · {len(pairs)} textos"
              + (" · glosario externo" if mounts else " · glosario de la imagen") + "\n")
        for p in pairs:
            same = p["text"].strip().lower() == (p.get("tr") or "").strip().lower()
            print("EN:", p["text"])
            print("ES:", p.get("tr", "") + ("   (sin cambios)" if same else ""))
            print()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
