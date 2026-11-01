"""El PDF de texto no debe tapar los dibujos vectoriales que hay detrás del texto."""
import subprocess
import sys
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("argostranslate")

ROOT = Path(__file__).resolve().parents[2]


def test_dibujo_bajo_el_texto_se_conserva(tmp_path):
    src = tmp_path / "in.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 250, 90), color=None, fill=(0.7, 0.1, 0.1))
    page.insert_text((60, 75), "Rnd 1: MR, 6 sc.", fontsize=11, color=(1, 1, 1))
    doc.save(src)

    out = tmp_path / "out.pdf"
    r = subprocess.run([sys.executable, str(ROOT / "translate_pdf_text.py"), str(src), "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # la zona del texto (con su margen) debe seguir siendo mayoritariamente roja, no un rectángulo blanco
    pix = pymupdf.open(out)[0].get_pixmap(clip=pymupdf.Rect(60, 66, 140, 77))
    px = [pix.pixel(x, y) for x in range(pix.width) for y in range(pix.height)]
    rojos = sum(1 for r, g, b in px if r > 150 and g < 60)
    assert rojos / len(px) > 0.5
