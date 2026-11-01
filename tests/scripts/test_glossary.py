"""Mecanismo del glosario (marcadores, siglas, mayúsculas y entradas con expresión regular).

Usa un traductor falso: se prueba la lógica del glosario, no la calidad de Argos.
Necesita las dependencias de los scripts (opencv...), no las de la web.
"""
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from translate_pdf import Translator, load_glossary  # noqa: E402


class Fake:
    """Devuelve el texto con unas pocas palabras cambiadas; respeta los marcadores Q<n>Q."""
    def translate(self, text, src, dst):
        return text.replace("Cut", "Corta").replace("Use", "Usa")


def tr(glossary_text, tmp_path):
    f = tmp_path / "g.txt"
    f.write_text(glossary_text, encoding="utf-8")
    t = Translator.__new__(Translator)
    t.tr, t.src, t.dst = Fake(), "en", "es"
    g = load_glossary(f)
    t.gl = sorted(((k, v) for k, v in g.items() if not k.startswith("post:")), key=lambda kv: -len(kv[0]))
    t.post = sorted(((k[5:], v) for k, v in g.items() if k.startswith("post:")), key=lambda kv: -len(kv[0]))
    return t


def test_term_is_replaced_and_case_is_kept(tmp_path):
    t = tr("plastic canvas = rejilla de plástico\n", tmp_path)
    assert t("Cut plastic canvas") == "Corta rejilla de plástico"
    assert t("Plastic canvas is stiff") == "Rejilla de plástico is stiff"
    assert t("PLASTIC CANVAS") == "REJILLA DE PLÁSTICO"


def test_acronym_kept_exactly_as_written(tmp_path):
    t = tr("SC = SC\nINC = INC\n", tmp_path)
    assert t("Use sc and Inc and SC") == "Usa sc and Inc and SC"


def test_whole_words_only(tmp_path):
    t = tr("st = st\ntail = hebra\n", tmp_path)
    assert t("1st stitch, tails and tail") == "1st stitch, tails and hebra"


def test_regex_entry_only_matches_in_context(tmp_path):
    t = tr(r"re:\bRound (\d+) = Vuelta \1" + "\n" + r"re:\bRounds (\d+(?:\s*[-–]\s*\d+)?) = Vueltas \1" + "\n", tmp_path)
    assert t("Round 3: sc around") == "Vuelta 3: sc around"
    assert t("Rounds 2-5: inc") == "Vueltas 2-5: inc"
    assert t("the upper round body") == "the upper round body"  # adjetivo: no se toca


def test_longest_entry_wins(tmp_path):
    t = tr("single crochet = punto bajo\ncrochet = ganchillo\n", tmp_path)
    assert t("single crochet and crochet") == "punto bajo and ganchillo"


def test_value_with_backslash_is_not_interpreted(tmp_path):
    t = tr(r"path = C:\temp" + "\n", tmp_path)
    assert t("path") == r"C:\temp"


def test_glossary_file_parsing(tmp_path):
    f = tmp_path / "g.txt"
    f.write_text("a = b  # comentario\nre:\\bRed(?=\\s+yarn\\b) = Rojo\n[después]\nwall = pared\n[antes]\nx=y\n", encoding="utf-8")
    g = load_glossary(f)
    assert g == {"a": "b", r"re:\bRed(?=\s+yarn\b)": "Rojo", "post:wall": "pared", "x": "y"}


def test_post_entries_only_fix_leftover_english(tmp_path):
    f = tmp_path / "g.txt"
    f.write_text("[después]\nwall = pared\nfront wall = pared frontal\nre:\\bMake (\\d+) = Hacer \\1\n", encoding="utf-8")
    t = Translator.__new__(Translator)
    t.tr, t.src, t.dst = Fake(), "en", "es"
    g = load_glossary(f)
    t.gl = []
    t.post = sorted(((k[5:], v) for k, v in g.items() if k.startswith("post:")), key=lambda kv: -len(kv[0]))
    assert t("Cut the Front Wall and the wall (Make 2)") == "Corta the Pared frontal and the pared (Hacer 2)"
    assert t("la pared walls") == "la pared walls"            # 'walls' no coincide con 'wall'
    assert t("el muro") == "el muro"                          # lo ya traducido no se toca


def test_marker_lost_by_the_model_falls_back_without_garbage(tmp_path):
    """Si el motor se come un marcador, se reintenta sin esa entrada: nunca queda un marcador a la vista."""
    class Eater:
        def translate(self, text, src, dst):
            return re.sub(r"ZQX\d+Z", "", text).replace("Cut", "Corta")
    t = tr("plastic canvas = rejilla de plástico\n", tmp_path)
    t.tr = Eater()
    out = t("Cut plastic canvas")
    assert "ZQX" not in out and out.startswith("Corta")


def test_broken_brackets_trigger_a_retry_with_fewer_markers(tmp_path):
    """Con demasiados marcadores el motor puede comerse un paréntesis: se reintenta quitando entradas."""
    class Clumsy:
        def translate(self, text, src, dst):
            out = text.replace("around", "alrededor")
            return out.replace("(", "", 1) if out.count("ZQX") >= 3 else out
    t = tr("sc = sc\ninc = inc\nsl st = sl st\n", tmp_path)
    t.tr = Clumsy()
    out = t("(sc, inc, sl st) around")
    assert out.count("(") == 1 and out.count(")") == 1 and "ZQX" not in out


def test_marker_does_not_collide_between_entries(tmp_path):
    # ZQX1Z y ZQX12Z no deben confundirse al restaurar
    entries = "".join(f"term{i} = valor{i}\n" for i in range(14))
    t = tr(entries, tmp_path)
    assert t("term1 term12 term13") == "valor1 valor12 valor13"


def test_repo_glossary_loads_and_has_no_verb_noun_traps():
    g = load_glossary(Path(__file__).resolve().parent.parent.parent / "glosario_ganchillo_es.txt")
    assert g["plastic canvas"] == "rejilla de plástico"
    # estas entradas convertían verbos/adjetivos en sustantivos
    assert "stitch" not in g and "round" not in g
    assert all(re.compile(k[3:]) for k in g if k.startswith("re:"))


def test_color_before_yarn_is_swapped():
    t = Translator.__new__(Translator)
    t.post = []
    fix = t._post
    assert fix("Marrón Hilo") == "Hilo marrón"
    assert fix("Blanco hilo (ribete de nieve)") == "Hilo blanco (ribete de nieve)"   # empieza en mayúscula
    assert fix("Utilizando verde hilo, hacer una hoja") == "Utilizando hilo verde, hacer una hoja"
    assert fix("Marrón claro hilo") == "Hilo marrón claro"
    assert fix("hilo marrón y cadena") == "hilo marrón y cadena"      # ya correcto: no se toca


def test_repo_glossary_entries_keep_brackets_balanced():
    """Si el destino tiene otros paréntesis que el origen, la frase se da por rota y se descarta la entrada."""
    g = load_glossary(Path(__file__).resolve().parent.parent.parent / "glosario_ganchillo_es.txt")
    bad = [k for k, v in g.items() if not k.startswith(("re:", "post:"))
           and any(k.count(c) != v.count(c) for c in "()[]")]
    assert not bad, bad


def test_paragraph_is_translated_sentence_by_sentence(tmp_path):
    calls = []

    class Spy:
        def translate(self, text, src, dst):
            calls.append(text)
            return text

    t = tr("", tmp_path)
    t.tr = Spy()
    out = t("Row 1: ch 24 (22). Row 2: ch 2 (22). Sew approx. 5 in. above the door; See photo (22).")
    assert calls == ["Row 1: ch 24 (22).", "Row 2: ch 2 (22).", "Sew approx. 5 in. above the door;", "See photo (22)."]
    assert out == "Row 1: ch 24 (22). Row 2: ch 2 (22). Sew approx. 5 in. above the door; See photo (22)."


def test_no_split_before_parenthesis_number_or_after_enumerator(tmp_path):
    calls = []

    class Spy:
        def translate(self, text, src, dst):
            calls.append(text)
            return text

    t = tr("", tmp_path)
    t.tr = Spy()
    t("Ch 23. Sc in 2nd ch across. (22)")
    t("Build order: A. Base and snow, B. Mitten body")
    t("1. Sew the base. 2. Attach the thumb")
    assert calls == ["Ch 23. Sc in 2nd ch across. (22)", "Build order: A. Base and snow, B. Mitten body",
                     "1. Sew the base. 2. Attach the thumb"]


def test_single_sentence_is_not_split(tmp_path):
    t = tr("", tmp_path)
    assert t("Rnd 1: MR, 6 sc.") == "Rnd 1: MR, 6 sc."


def test_split_falls_back_to_whole_text_if_the_model_drops_a_bracket(tmp_path):
    class Lossy:
        def translate(self, text, src, dst):
            return text.replace("(note) ", "") if text.startswith("(note)") and len(text) < 20 else text

    t = tr("", tmp_path)
    t.tr = Lossy()
    text = "(note) Ch 1, turn. Sc 7, ch 6."
    assert t(text) == text  # la traducción por frases perdería "(note)"; se usa el texto entero

def test_tan_is_a_colour_only_in_colour_contexts(tmp_path):
    t = tr("tan sock = calcetín beige/camel\nre:\\btan(?=[,:.)]) = beige/camel\n", tmp_path)
    assert t("Frame (tan): MR") == "Frame (beige/camel): MR"
    assert t("With tan, ch 5") == "With beige/camel, ch 5"
    assert t("Tan sock: ch 5") == "Calcetín beige/camel: ch 5"
    assert t("tangled yarn") == "tangled yarn"  # no es una palabra completa
    assert t("so tan yet") == "so tan yet"       # "tan" sin coma/paréntesis detrás no se toca
