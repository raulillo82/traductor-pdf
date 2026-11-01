#!/usr/bin/env python3
"""Traduce un PDF con capa de texto real (no escaneado) sustituyendo el texto en el propio PDF.

Local: PyMuPDF + Argos Translate. Mantiene el PDF vectorial (texto seleccionable, poco peso).
Para PDFs hechos de imágenes usa translate_pdf.py.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf  # noqa: E402

from translate_pdf import Translator, load_glossary, parse_pages  # noqa: E402

# métricas de Helvetica, con viñetas y tildes
LIB = next(p for p in ("/usr/share/fonts/truetype/LiberationSans-%s.ttf",
                       "/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf")
           if os.path.exists(p % "Regular"))
BULLET = re.compile(r"^\s*([•\-–·▪])\s+")


def block_info(b):
    spans = [s for l in b["lines"] for s in l["spans"]]
    text = " ".join(" ".join(s["text"] for s in l["spans"]).strip() for l in b["lines"])
    text = re.sub(r"\s+", " ", text.replace("**", "")).strip()
    size = max(s["size"] for s in spans)
    bold = all("bold" in s["font"].lower() for s in spans if s["text"].strip())
    return text, size, bold, spans[0]["color"], len(b["lines"])


def split_rows(block):
    """Divide un bloque en bloques más pequeños cuando sus líneas están en la MISMA fila.

    Los diagramas ponen la etiqueta, el número de la barra y el signo +/- como líneas de un mismo
    bloque pero lado a lado. Un párrafo de verdad tiene las líneas una debajo de otra; unirlas en un
    solo texto mezcla las etiquetas y las mueve de sitio.
    """
    groups = []
    for line in block["lines"]:
        y0, y1 = line["bbox"][1], line["bbox"][3]
        if groups:
            py0, py1 = groups[-1][-1]["bbox"][1], groups[-1][-1]["bbox"][3]
            if min(y1, py1) - max(y0, py0) > 0.5 * min(y1 - y0, py1 - py0):  # solapa en vertical: misma fila
                groups.append([line])
                continue
            groups[-1].append(line)
        else:
            groups.append([line])
    if len(groups) == 1:
        return [block]
    return [{**block, "lines": g, "bbox": (min(l["bbox"][0] for l in g), min(l["bbox"][1] for l in g),
                                           max(l["bbox"][2] for l in g), max(l["bbox"][3] for l in g))}
            for g in groups]


def right_limit(r, others, default):
    """Hasta dónde puede crecer a la derecha el texto traducido de `r` sin pisar a un vecino en su misma franja."""
    near = [o.x0 for o in others if o.x0 >= r.x1 - 1 and min(o.y1, r.y1) - max(o.y0, r.y0) > 0]
    return min([default] + [x - 4 for x in near]) if near else default


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output")
    ap.add_argument("--pages")
    ap.add_argument("--glossary")
    ap.add_argument("--from-code", default="en")
    ap.add_argument("--to-code", default="es")
    ap.add_argument("--dump-json", help="guarda los pares origen/traducción por página (para revisar)")
    a = ap.parse_args()

    src = Path(a.pdf)
    out = Path(a.output or src.with_name(src.stem + "_es.pdf"))
    doc = pymupdf.open(src)
    tr = Translator(a.from_code, a.to_code, load_glossary(a.glossary))
    cache, overflow, pairs = {}, 0, []

    for n in parse_pages(a.pages, len(doc)):
        page = doc[n - 1]
        W = page.rect.width
        jobs = []
        blocks = [sb for b in page.get_text("dict")["blocks"] if b["type"] == 0 and b["lines"] for sb in split_rows(b)]
        rects = [pymupdf.Rect(b["bbox"]) for b in blocks]  # antes de borrar el texto
        for b in blocks:
            text, size, bold, color, nl = block_info(b)
            if not re.search(r"[A-Za-z]{2}", text):
                continue
            m = BULLET.match(text)
            body = text[m.end():] if m else text
            if body not in cache:
                cache[body] = tr(body)
            pairs.append({"page": n, "text": body, "tr": cache[body]})
            jobs.append((pymupdf.Rect(b["bbox"]), (m.group(1) + " " if m else "") + cache[body], size, bold, color, nl))

        for r, *_ in jobs:
            page.add_redact_annot(r + (-1, -1, 1, 1), fill=False)  # sin relleno: no tapar dibujos vectoriales que hay detrás
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)

        for r, text, size, bold, color, nl in jobs:
            centered = nl == 1 and abs((r.x0 + r.x1) / 2 - W / 2) < 6 and r.width < W * 0.9
            right = max(r.x1, W - r.x0) if not centered else W - r.x0
            others = [o for o in rects if o != r]
            box = pymupdf.Rect(r.x0, r.y0 - 1, max(r.x1, right_limit(r, others, right)) if not centered else right, r.y1 + size * 0.4)
            rgb = ((color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255)
            fs = size
            while fs >= 5:
                rc = page.insert_textbox(box, text, fontsize=fs, fontname="LibB" if bold else "LibR", fontfile=LIB % ("Bold" if bold else "Regular"),
                                         color=rgb, align=1 if centered else 0)
                if rc >= 0:
                    break
                fs -= 0.5  # no cabe: se reintenta más pequeño (insert_textbox no dibuja si falla)
            else:
                overflow += 1
        print(f"Página {n} lista", file=sys.stderr)

    if a.dump_json:
        import json
        Path(a.dump_json).write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    doc.subset_fonts()
    doc.save(out, garbage=3, deflate=True)
    if overflow:
        print(f"Aviso: {overflow} bloques no cupieron ni a 5 pt", file=sys.stderr)
    print(f"Hecho: {out}")


if __name__ == "__main__":
    main()
