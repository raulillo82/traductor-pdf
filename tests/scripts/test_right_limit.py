"""El texto traducido no debe crecer por encima de un vecino que tiene a su derecha."""
import pytest

pymupdf = pytest.importorskip("pymupdf")
from translate_pdf_text import right_limit  # noqa: E402

R = pymupdf.Rect


def test_se_detiene_antes_del_vecino_de_la_misma_franja():
    etiqueta = R(191, 69, 267, 77)
    parrafo = R(308, 60, 564, 140)
    assert right_limit(etiqueta, [parrafo], 600) == 304


def test_ignora_vecinos_en_otra_franja_o_a_la_izquierda():
    etiqueta = R(191, 69, 267, 77)
    assert right_limit(etiqueta, [R(308, 100, 564, 140), R(40, 60, 150, 80)], 600) == 600
