#!/usr/bin/env python3
"""Traduce (EN->ES por defecto) un PDF hecho de imágenes conservando el diseño.

Todo corre en local: pdftoppm + Tesseract (OCR) + Argos Translate + OpenCV.
No usa ninguna API externa. Ver RESUMEN_translate_pdf.md.
"""
import argparse
import html
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def _first_existing(*paths):
    return next((p for p in paths if os.path.exists(p)), paths[0])


DEFAULT_FONT = _first_existing("/usr/share/fonts/truetype/DejaVuSans.ttf",
                               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SS = 3  # supermuestreo al dibujar texto


# ---------------------------------------------------------------- utilidades
def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return [p for p in out if 1 <= p <= total]


def load_glossary(path):
    """Lee `origen = destino` (una por línea, `#` para comentarios).

    Una línea `[después]` pasa a las entradas que se aplican DESPUÉS de traducir, solo a palabras que
    el motor dejó en inglés (se guardan con el prefijo `post:`); `[antes]` vuelve a las normales.
    """
    g, mode = {}, ""
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("[") and line.endswith("]"):
                mode = "post:" if line.strip("[]").strip().lower() == "después" else ""
            elif " = " in line or "=" in line:
                # El separador es el ÚLTIMO " = " (así una regex puede llevar "=" dentro, p. ej. un lookahead)
                k, v = line.rsplit(" = ", 1) if " = " in line else line.split("=", 1)
                g[mode + k.strip()] = v.strip()
    return g


def rasterize_page(pdf, n, outdir, dpi):
    outdir.mkdir(parents=True, exist_ok=True)
    base = outdir / f"p{n:04d}"
    subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(n), "-l", str(n), "-png", "-singlefile", str(pdf), str(base)], check=True)
    return base.with_suffix(".png")


# ---------------------------------------------------------------------- OCR
BIG_TEXT_RATIO = 2.5   # "grande" = altura >= 2,5 veces la mediana de la página
BIG_TEXT_MIN_CONF = 88  # a un texto grande (cabeceras manuscritas...) se le exige mucha más confianza


def keep_item(conf, lh, median_lh, min_conf):
    """¿Se traduce este texto? Los grandes y dudosos (letra decorativa mal leída) se dejan como están."""
    if conf < min_conf + 20:
        return False
    if median_lh and lh >= BIG_TEXT_RATIO * median_lh and conf < BIG_TEXT_MIN_CONF:
        return False
    return True

def ocr_page(args):
    path, lang, psm, min_conf, *rest = args
    merge = rest[0] if rest else True
    img = cv2.imread(str(path))
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    d = pytesseract.image_to_data(
        cv2.cvtColor(big, cv2.COLOR_BGR2RGB), lang=lang,
        config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    heights = [d["height"][i] / 2 for i, w in enumerate(d["text"]) if w.strip() and float(d["conf"][i]) >= min_conf]
    median_lh = statistics.median(heights) if heights else 0
    groups = {}
    for i, txt in enumerate(d["text"]):
        txt = txt.strip()
        if not txt or float(d["conf"][i]) < min_conf:
            continue
        key = (d["block_num"][i], d["par_num"][i])
        groups.setdefault(key, []).append(i)
    items = []
    for idxs in groups.values():
        lines = {}
        for i in idxs:
            lines.setdefault(d["line_num"][i], []).append(i)
        text = " ".join(" ".join(d["text"][i].strip() for i in sorted(l, key=lambda i: d["left"][i]))
                        for _, l in sorted(lines.items()))
        letters = sum(c.isalpha() for c in text)
        digits = sum(c.isdigit() for c in text)
        if letters < 3 and not (digits and letters):
            continue
        if letters / max(1, len(text.replace(" ", ""))) < 0.25 and digits < 2:
            continue
        x0 = min(d["left"][i] for i in idxs) // 2
        y0 = min(d["top"][i] for i in idxs) // 2
        x1 = max(d["left"][i] + d["width"][i] for i in idxs) // 2
        y1 = max(d["top"][i] + d["height"][i] for i in idxs) // 2
        lh = statistics.median(d["height"][i] for i in idxs) / 2
        conf = statistics.mean(float(d["conf"][i]) for i in idxs)
        if not keep_item(conf, lh, median_lh, min_conf):  # lectura dudosa (rótulos decorativos, fotos)
            continue
        items.append({"box": [x0, y0, x1, y1], "text": text, "nlines": len(lines), "lh": lh, "conf": round(conf)})
    items.sort(key=lambda t: (t["box"][1], t["box"][0]))
    return merge_lines(items) if merge else items


# ------------------------------------------------------- unir líneas de un párrafo
TERMINAL = ".!?:;"


def merge_lines(items):
    """Une líneas OCR consecutivas que son un mismo párrafo partido a mitad de frase.

    Tesseract (psm 11) entrega cada línea suelta, y traducirlas por separado rompe el sentido
    ("cómo ganchillo"). Solo se unen dos líneas A y B si: B está justo debajo y es del mismo tamaño,
    ambas comparten borde izquierdo (o centro), A no termina la frase y B sigue en minúscula, y A llega
    casi hasta el final de la columna (una lista de elementos cortos no cumple eso).
    """
    n = len(items)
    if n < 2:
        return items

    def geom(i):
        x0, y0, x1, y1 = items[i]["box"]
        return x0, y0, x1, y1, items[i]["lh"], (x0 + x1) / 2

    def aligned(i, j):
        """'left' / 'center' / None según cómo se alinean i (arriba) y j (debajo)."""
        ax0, _, _, _, lh, acx = geom(i)
        bx0, _, _, _, _, bcx = geom(j)
        dl, dc = abs(ax0 - bx0), abs(acx - bcx)
        if dc <= 1.0 * lh and dc < dl:
            return "center"
        return "left" if dl <= 1.5 * lh else None

    def column_width(i, how):
        x0, y0, x1, y1, lh, cx = geom(i)
        best = x1 - x0
        for k in range(n):
            kx0, ky0, kx1, _, klh, kcx = geom(k)
            if not 0.75 <= klh / lh <= 1.33 or abs(ky0 - y0) > 14 * lh:
                continue
            if (how == "left" and abs(kx0 - x0) <= 1.5 * lh) or (how == "center" and abs(kcx - cx) <= 1.0 * lh):
                best = max(best, kx1 - kx0)
        return best

    def continues(i, j):
        a, b = items[i]["text"].rstrip(), items[j]["text"].lstrip()
        if not a or not b or a[-1] in TERMINAL:
            return False
        return b[0].islower() or a.endswith(",")

    nxt, has_prev = {}, set()
    for i in sorted(range(n), key=lambda k: geom(k)[1]):
        x0, y0, x1, y1, lh, cx = geom(i)
        best = None
        for j in range(n):
            if j == i or j in has_prev:
                continue
            _, jy0, _, _, jlh, _ = geom(j)
            gap = jy0 - y1
            if not (-0.2 * lh <= gap <= 0.9 * max(lh, jlh)) or not 0.75 <= jlh / lh <= 1.33:
                continue
            how = aligned(i, j)
            if how is None or not continues(i, j):
                continue
            if (x1 - x0) < 0.8 * column_width(i, how):
                continue
            if best is None or gap < best[0]:
                best = (gap, j, how)
        if best:
            nxt[i] = best[1:]
            has_prev.add(best[1])

    merged, used = [], set()
    for i in sorted(range(n), key=lambda k: (geom(k)[1], geom(k)[0])):
        if i in used or i in has_prev:
            continue
        chain, hows, cur = [i], [], i
        while cur in nxt:
            cur, how = nxt[cur]
            chain.append(cur)
            hows.append(how)
        used.update(chain)
        if len(chain) == 1:
            merged.append(items[i])
            continue
        text = items[chain[0]]["text"].strip()
        for k in chain[1:]:
            t = items[k]["text"].strip()
            text = text[:-1] + t if text.endswith("-") and t[:1].islower() else text + " " + t
        boxes = [items[k]["box"] for k in chain]
        merged.append({"box": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                               max(b[2] for b in boxes), max(b[3] for b in boxes)],
                       "text": text, "nlines": sum(items[k]["nlines"] for k in chain),
                       "lh": sum(items[k]["lh"] for k in chain) / len(chain),
                       "center": all(h == "center" for h in hows)})
    return merged


# ---------------------------------------------------------------- traducción
def _match_case(src, out):
    """Adapta las mayúsculas del término del glosario a las del texto original."""
    if len(src) > 1 and src.isupper():
        return out.upper()
    if src[:1].isupper():
        return out[:1].upper() + out[1:]
    return out



class Translator:
    def __init__(self, src, dst, glossary):
        # Stanza consulta GitHub en cada Pipeline por defecto; los modelos ya son locales.
        import functools

        import argostranslate.package as pkg
        import argostranslate.translate as tr
        import stanza
        stanza.Pipeline = functools.partial(stanza.Pipeline, download_method=None)
        langs = {l.code for l in tr.get_installed_languages()}
        if not {src, dst} <= langs:
            print(f"Descargando modelo Argos {src}->{dst} (solo la primera vez)…", file=sys.stderr)
            pkg.update_package_index()
            av = [p for p in pkg.get_available_packages() if p.from_code == src and p.to_code == dst]
            if not av:
                sys.exit(f"No hay modelo Argos {src}->{dst}")
            pkg.install_from_path(av[0].download())
        self.tr, self.src, self.dst = tr, src, dst
        self.gl = sorted(((k, v) for k, v in glossary.items() if not k.startswith("post:")), key=lambda kv: -len(kv[0]))
        self.post = sorted(((k[5:], v) for k, v in glossary.items() if k.startswith("post:")), key=lambda kv: -len(kv[0]))

    def _attempt(self, text, skip):
        """Traduce `text` protegiendo con marcadores (ZQX<n>Z) las entradas del glosario salvo `skip`.

        Devuelve (traducción, entradas_usadas, íntegra). Íntegra = no se perdió ningún marcador y
        siguen igual los paréntesis y corchetes (con demasiados marcadores el motor puede comerse alguno).
        """
        marks, owner, work = {}, {}, text
        for idx, (k, v) in enumerate(self.gl):
            if idx in skip:
                continue
            regex = k.startswith("re:")  # "re:Round (\\d+) = Vuelta \\1": solo en ese contexto
            if regex:
                pat = re.compile(k[3:], re.I)
            else:
                pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", re.I)

            def sub(m, v=v, k=k, regex=regex, idx=idx):
                tag = f"ZQX{len(marks)}Z"
                owner[tag] = idx
                if not regex and v.upper() == k.upper():  # "SC = SC": se deja tal cual estaba escrito
                    marks[tag] = m.group(0)
                else:
                    marks[tag] = _match_case(m.group(0), m.expand(v) if regex else v)
                return tag
            work = pat.sub(sub, work)
        out = self.tr.translate(work, self.src, self.dst)
        ok = all(re.search(re.escape(t), out, re.I) for t in marks) and \
            all(work.count(c) == out.count(c) for c in "()[]")
        for tag, v in marks.items():
            out = re.sub(re.escape(tag), lambda _m, v=v: v, out, flags=re.I)
        return out, sorted(set(owner.values())), ok

    COLORS = ("marrón", "blanco", "verde", "rojo", "crema", "rosa", "gris", "negro", "naranja", "amarillo", "dorado", "beige", "azul")
    YARN_ORDER = re.compile(r"\b((?:" + "|".join(COLORS) + r")(?: (?:claro|oscuro|dorado|más oscuro))?) hilo\b", re.I)

    def _fix_yarn_order(self, out):
        """El motor deja 'Brown Yarn' como 'Marrón hilo': en español el color va detrás ('hilo marrón')."""
        def swap(m):
            hilo = "Hilo" if m.group(1)[0].isupper() else "hilo"
            return f"{hilo} {m.group(1).lower()}"
        return self.YARN_ORDER.sub(swap, out)

    def _post(self, out):
        """Traduce las palabras que el motor dejó en inglés (sin tocar la gramática de lo ya traducido)."""
        letters = "A-Za-z0-9áéíóúüñÁÉÍÓÚÜÑ"
        for k, v in self.post:
            regex = k.startswith("re:")
            pat = re.compile(k[3:] if regex else rf"(?<![{letters}])" + re.escape(k) + rf"(?![{letters}])", re.I)
            out = pat.sub(lambda m, v=v, regex=regex: _match_case(m.group(0), m.expand(v) if regex else v), out)
        return self._fix_yarn_order(out)

    # Fin de frase seguido de mayúscula o comillas ("... (22). Row 2: ..."). No corta: antes de "(" ni de un número
    # ("(5)" suelto pierde el paréntesis), ni tras un enumerador ("A. Base", "1. Sew") ni tras "approx. 5 in.".
    SENTENCE = re.compile(r"(?<![\s(][A-Za-z]\.)(?<!^[A-Za-z]\.)(?<!\d\.)(?<=[.;])\s+(?=[A-Z\"“])")

    def __call__(self, text):
        """Traduce frase a frase: con un párrafo entero de instrucciones el motor deja trozos en inglés."""
        parts = self.SENTENCE.split(text)
        if len(parts) > 1:
            outs = [self._translate_one(p) for p in parts]
            # Si al partir el motor se come un paréntesis o corchete (se pierde texto), se traduce entero como antes.
            if all(p.count(c) == o.count(c) for p, o in zip(parts, outs) for c in "()[]"):
                return " ".join(outs)
        return self._translate_one(text)

    def _translate_one(self, text):
        out, used, ok = self._attempt(text, frozenset())
        if not ok:
            # Se quitan las entradas de una en una (primero las traducciones, luego las siglas)
            # hasta que la frase salga íntegra; si ninguna basta, sin glosario.
            def is_acronym(i):
                k, v = self.gl[i]
                return not k.startswith("re:") and v.upper() == k.upper()
            for idx in sorted(used, key=is_acronym):
                cand, _, good = self._attempt(text, frozenset({idx}))
                if good:
                    out = cand
                    break
            else:
                out, _, _ = self._attempt(text, frozenset(range(len(self.gl))))
        return self._post(html.unescape(out).replace("&quot;", "&"))


# ------------------------------------------------------------------- borrado
def erase(img, items):
    """Borra el texto original dentro de cada caja; devuelve imagen y color del texto por ítem."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = np.zeros(gray.shape, np.uint8)
    h, w = gray.shape
    for it in items:
        x0, y0, x1, y1 = it["box"]
        pad = max(2, int(it["lh"] * 0.2))
        x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        bg = np.median(np.concatenate([roi[0], roi[-1], roi[:, 0], roi[:, -1]]))
        dark = np.mean(roi) >= bg - 1 and np.percentile(roi, 5) < bg - 20 or bg > 128
        diff = (bg - roi.astype(np.int16)) if dark else (roi.astype(np.int16) - bg)
        m = (diff > 40).astype(np.uint8) * 255
        # color del texto: media de los píxeles más extremos
        sel = diff > max(40, np.percentile(diff, 95) * 0.6)
        col = img[y0:y1, x0:x1][sel].mean(axis=0) if sel.any() else (np.array([20, 20, 20]) if dark else np.array([240, 240, 240]))
        it["color"] = tuple(int(c) for c in col[::-1])
        k = max(3, int(it["lh"] * 0.25) | 1)
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        mask[y0:y1, x0:x1] |= m
    return cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)


# ---------------------------------------------------------------- escritura
def wrap(draw, text, font, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=font) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def write_text(img, items, font_path):
    H, W = img.shape[:2]
    big = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).resize((W * SS, H * SS), Image.LANCZOS)
    dr = ImageDraw.Draw(big)
    for it in items:
        tr = it.get("tr")
        if not tr:
            continue
        x0, y0, x1, y1 = [v * SS for v in it["box"]]
        bw, bh = x1 - x0, y1 - y0
        single = it["nlines"] == 1
        centered = it.get("center") or (single and bw < W * SS * 0.4)
        maxw = bw * 1.25 if not single else max(bw * 1.25, bw + 20 * SS)
        if it["nlines"] > 1:  # un párrafo ya es ancho: mismo ancho de columna y más líneas (no pisar fotos ni columnas vecinas)
            maxw = bw * 1.03
        cxb = (x0 + x1) / 2
        room = (2 * min(cxb, W * SS - cxb) if centered else W * SS - x0) - 6 * SS  # que no se salga de la página
        maxw = max(min(maxw, room), bw * 0.9)
        size = max(8, int(it["lh"] * SS * 0.95))
        while True:
            font = ImageFont.truetype(font_path, size)
            lines = wrap(dr, tr, font, maxw)
            lh = size * 1.2
            if size <= 8 or (len(lines) * lh <= max(bh * (1.05 if it["nlines"] > 1 else 1.15), lh) and all(dr.textlength(l, font=font) <= maxw * 1.02 for l in lines)):
                break
            size -= 1
        total = len(lines) * lh
        y = y0 + (bh - total) / 2 if single or total < bh else y0
        cx = (x0 + x1) / 2
        for l in lines:
            lw = dr.textlength(l, font=font)
            x = cx - lw / 2 if centered else x0
            dr.text((x, y), l, font=font, fill=it.get("color", (20, 20, 20)))
            y += lh
    out = big.resize((W, H), Image.LANCZOS)
    return cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)


# ------------------------------------------------------------ pipeline/página
_T = None


def _init(src, dst, glossary):
    global _T
    if glossary is not None:
        _T = Translator(src, dst, glossary)


def process_page(job):
    n, a, items = job  # a: dict de opciones
    png = rasterize_page(a["pdf"], n, Path(a["work"]) / "pages", a["dpi"])
    if items is None:
        items = ocr_page((png, a["src_lang"], a["psm"], a["min_conf"], a["merge"]))
        if _T is not None:
            for it in items:
                it["tr"] = _T(it["text"])
    if _T is None and not any("tr" in it for it in items):
        return n, items, None
    img = cv2.imread(str(png))
    img = write_text(erase(img, items), items, a["font"])
    jp = Path(a["work"]) / "out" / f"{n:03d}.jpg"
    cv2.imwrite(str(jp), img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not a["keep_images"]:
        png.unlink(missing_ok=True)
    return n, items, (img.shape[1], img.shape[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output")
    ap.add_argument("--pages")
    ap.add_argument("--dpi", type=int, default=130)
    ap.add_argument("--glossary")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--psm", type=int, default=11)
    ap.add_argument("--min-conf", type=float, default=40)
    ap.add_argument("--src-lang", default="eng", help="código Tesseract")
    ap.add_argument("--from-code", default="en")
    ap.add_argument("--to-code", default="es")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--from-json", help="translations.json con campo 'tr' por ítem")
    ap.add_argument("--keep-images", action="store_true")
    ap.add_argument("--no-merge-lines", action="store_true", help="no unir líneas del mismo párrafo antes de traducir")
    a = ap.parse_args()

    pdf = Path(a.pdf)
    outpdf = Path(a.output or pdf.with_name(pdf.stem + "_es.pdf"))
    work = outpdf.with_name(outpdf.stem + "_work")
    (work / "out").mkdir(parents=True, exist_ok=True)
    total = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout).group(1))
    pages = parse_pages(a.pages, total)

    # instalar el modelo (si falta) una sola vez antes de lanzar los procesos
    gl = None if (a.extract_only or a.from_json) else load_glossary(a.glossary)
    if gl is not None:
        Translator(a.from_code, a.to_code, gl)
    saved = json.loads(Path(a.from_json).read_text(encoding="utf-8")) if a.from_json else {}
    opts = dict(pdf=str(pdf), work=str(work), dpi=a.dpi, src_lang=a.src_lang, psm=a.psm,
                min_conf=a.min_conf, font=a.font, keep_images=a.keep_images, merge=not a.no_merge_lines)
    jobs = [(n, opts, saved.get(str(n))) for n in pages]

    print(f"Procesando {len(pages)} páginas con {min(a.workers, len(pages))} procesos…", file=sys.stderr)
    data, sizes = {}, {}
    with ProcessPoolExecutor(min(a.workers, len(pages)), initializer=_init, initargs=(a.from_code, a.to_code, gl)) as ex:
        for n, items, size in ex.map(process_page, jobs):
            data[str(n)], sizes[n] = items, size
            print(f"Página {n} lista", file=sys.stderr)

    name = "source.json" if a.extract_only else "translations.json"
    (work / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.extract_only:
        print(f"Textos en {work / name}")
        return
    c = None
    for n in pages:
        w, h = sizes[n]
        pw, ph = w * 72 / a.dpi, h * 72 / a.dpi
        c = c or canvas.Canvas(str(outpdf), pagesize=(pw, ph))
        c.setPageSize((pw, ph))
        c.drawImage(ImageReader(str(work / "out" / f"{n:03d}.jpg")), 0, 0, pw, ph)
        c.showPage()
    c.save()
    shutil.rmtree(work / "pages", ignore_errors=True) if not a.keep_images else None
    print(f"Hecho: {outpdf}")


if __name__ == "__main__":
    main()
