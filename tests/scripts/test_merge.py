"""Unión de líneas OCR en párrafos (translate_pdf.merge_lines)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from translate_pdf import merge_lines  # noqa: E402


def line(text, x0, y0, w=300, lh=20):
    return {"box": [x0, y0, x0 + w, y0 + lh], "text": text, "nlines": 1, "lh": lh}


def texts(items):
    return [i["text"] for i in items]


def test_wrapped_paragraph_is_merged():
    items = [line("This page shows how to crochet", 50, 100),
             line("the snowman's upper round body", 50, 124, 290),
             line("section and head using simple", 50, 148, 295),
             line("amigurumi techniques.", 50, 172, 150)]
    out = merge_lines(items)
    assert texts(out) == ["This page shows how to crochet the snowman's upper round body "
                          "section and head using simple amigurumi techniques."]
    assert out[0]["nlines"] == 4 and out[0]["box"] == [50, 100, 350, 192]


def test_list_items_are_not_merged():
    items = [line("White (body and head)", 50, 100, 200), line("Orange (carrot nose)", 50, 124, 190),
             line("Red (hat and scarf)", 50, 148, 180)]
    assert len(merge_lines(items)) == 3


def test_lowercase_short_list_items_are_not_merged():
    # empiezan en minúscula, pero la línea de arriba no llega al final de la columna
    items = [line("polyester stuffing", 50, 100, 140), line("yarn needle", 50, 124, 90),
             line("This long line makes the column wide enough to matter here", 50, 300, 340)]
    assert len(merge_lines(items)) == 3


def test_sentence_end_stops_the_merge():
    items = [line("Fasten off and weave in the end.", 50, 100), line("then stuff the head", 50, 124)]
    assert len(merge_lines(items)) == 2


def test_different_size_headings_are_not_merged():
    items = [line("Upper Body and", 50, 100, 300, lh=40), line("head are done", 50, 144, 300, lh=20)]
    assert len(merge_lines(items)) == 2


def test_two_columns_do_not_mix():
    items = [line("left column first line of", 40, 100, 200), line("right column first line of", 400, 100, 200),
             line("text that continues here", 40, 124, 200), line("text that continues there", 400, 124, 200)]
    out = merge_lines(items)
    assert sorted(texts(out)) == ["left column first line of text that continues here",
                                  "right column first line of text that continues there"]


def test_too_far_apart_vertically_is_not_merged():
    items = [line("first line of a paragraph that", 50, 100), line("continues far below", 50, 200)]
    assert len(merge_lines(items)) == 2


def test_hyphenated_word_is_joined():
    items = [line("amigu-", 50, 100, 300), line("rumi techniques", 50, 124, 300)]
    assert texts(merge_lines(items)) == ["amigurumi techniques"]


def test_centered_caption_is_merged_and_marked_centered():
    items = [line("a caption that is centered and", 105, 100, 190), line("goes on a second line", 125, 124, 150)]
    # mismo centro (200), distinto borde izquierdo
    items[0]["box"] = [105, 100, 295, 120]
    items[1]["box"] = [125, 124, 275, 144]
    out = merge_lines(items)
    assert len(out) == 1 and out[0]["center"] is True


def test_merge_does_not_change_unrelated_items_or_order():
    items = [line("Title", 50, 20, 100, lh=40), line("alone", 400, 500, 60)]
    assert texts(merge_lines(items)) == ["Title", "alone"]
    assert merge_lines([]) == [] and len(merge_lines(items[:1])) == 1


# ---- qué textos se traducen (cabeceras decorativas mal leídas se dejan como están)
def test_keep_item_rules():
    from translate_pdf import keep_item
    assert keep_item(96, 14, 14, 40)                 # texto normal, buena lectura
    assert not keep_item(55, 14, 14, 40)             # texto normal, lectura dudosa
    assert keep_item(96, 40, 14, 40)                 # grande y muy bien leído (cinta de sección)
    assert not keep_item(73, 90, 14, 40)             # grande y a medias: cabecera manuscrita ("f. J 40 Snowman")
    assert not keep_item(76, 100, 14, 40)            # "Cream Cottage Foundation" leído al 76 %
    assert keep_item(70, 30, 14, 40)                 # algo grande pero por debajo del umbral de "grande"
    assert keep_item(80, 14, 0, 40)                  # sin mediana conocida no se aplica la regla
