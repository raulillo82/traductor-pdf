#!/usr/bin/env python3
"""Punto de entrada del contenedor: PDF por stdin -> PDF traducido por stdout.

Protocolo por stderr (una línea por evento, prefijo @@):
  @@MODE text|image   @@PAGES n   @@PROGRESS 0..100   @@ERROR mensaje   @@LOG detalle (solo para el log)
Códigos de salida: 0 ok, 2 entrada inválida, 3 límite superado, 4 fallo al traducir.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = int(os.environ.get("MAX_BYTES", 100 * 1024 * 1024))
MAX_PAGES = int(os.environ.get("MAX_PAGES", 100))
TIMEOUT = int(os.environ.get("JOB_TIMEOUT", 1800))
GLOSSARY = os.environ.get("GLOSSARY", "/app/glosario_ganchillo_es.txt")
TEXT_CHARS_PER_PAGE = 100  # por debajo se considera PDF de imágenes


def emit(kind, value=""):
    print(f"@@{kind} {value}".rstrip(), file=sys.stderr, flush=True)


def fail(code, msg):
    emit("ERROR", msg)
    sys.exit(code)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def main():
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        fail(3, f"El PDF supera el tamaño máximo ({MAX_BYTES // 1024 // 1024} MB).")
    if not data.startswith(b"%PDF"):
        fail(2, "El fichero no es un PDF válido.")

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp, "in.pdf"), Path(tmp, "out.pdf")
        src.write_bytes(data)
        info = run(["pdfinfo", str(src)])
        m = re.search(r"Pages:\s+(\d+)", info.stdout)
        if info.returncode or not m:
            fail(2, "No se puede leer el PDF (¿está dañado o protegido con contraseña?).")
        pages = int(m.group(1))
        if pages > MAX_PAGES:
            fail(3, f"El PDF tiene {pages} páginas y el máximo es {MAX_PAGES}.")
        emit("PAGES", pages)

        sample = run(["pdftotext", "-l", "5", str(src), "-"]).stdout
        text_pages = min(pages, 5)
        mode = "text" if len(sample.strip()) / text_pages >= TEXT_CHARS_PER_PAGE else "image"
        emit("MODE", mode)

        script = "translate_pdf_text.py" if mode == "text" else "translate_pdf.py"
        cmd = [sys.executable, f"/app/{script}", str(src), "-o", str(dst), "--glossary", GLOSSARY]
        done, tail = 0, []
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            for line in p.stderr:
                tail = (tail + [line.rstrip()])[-6:]
                if re.match(r"Página \d+ lista", line):
                    done += 1
                    emit("PROGRESS", min(99, done * 100 // pages))
            p.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            fail(4, "La traducción tardó demasiado y se canceló.")
        if p.returncode or not dst.exists():
            for l in tail:
                emit("LOG", l)
            fail(4, "No se pudo traducir el PDF.")
        emit("PROGRESS", 100)
        sys.stdout.buffer.write(dst.read_bytes())


if __name__ == "__main__":
    main()
