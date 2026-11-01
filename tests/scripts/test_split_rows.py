"""Las líneas de un bloque que están en la misma fila (etiquetas de diagramas) no se unen en un párrafo."""
import pytest

pytest.importorskip("pymupdf")
from translate_pdf_text import split_rows  # noqa: E402


def line(x0, y0, x1, y1, text):
    return {"bbox": (x0, y0, x1, y1), "spans": [{"text": text}]}


def test_etiqueta_numero_y_signo_en_la_misma_fila_se_separan():
    block = {"bbox": (45, 194, 227, 204), "lines": [line(45, 194, 70, 204, "Row 17"), line(130, 194, 136, 204, "2"),
                                                    line(220, 195, 227, 203, "-")]}
    parts = split_rows(block)
    assert [p["lines"][0]["spans"][0]["text"] for p in parts] == ["Row 17", "2", "-"]
    assert parts[0]["bbox"] == (45, 194, 70, 204)


def test_parrafo_normal_no_se_divide():
    block = {"bbox": (308, 60, 564, 90), "lines": [line(308, 60, 560, 70, "Panel Row 1: ch 24,"),
                                                   line(308, 71, 540, 81, "Row 2: ch 2, hdc"),
                                                   line(308, 82, 500, 92, "Rows 3-17: repeat")]}
    assert split_rows(block) == [block]
