"""promote_candidate de deploy/worker-deploy.sh, con un podman falso que solo entiende etiquetas e IDs.

Es la parte del script que modifica producción; ya falló dos veces (untag sin nombre quitaba latest, y un
candidato idéntico a latest sobrescribía previous). El podman falso guarda en un JSON qué ID tiene cada etiqueta.
"""
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "worker-deploy.sh"
IMAGE = "localhost/zz"

FAKE_PODMAN = r'''#!/usr/bin/env python3
import json, sys
path = sys.argv[0].rsplit("/", 1)[0] + "/state.json"
state = json.load(open(path))          # {"tags": {nombre: id}, "images": [ids]}
tags, args = state["tags"], sys.argv[1:]
def done(code=0):
    json.dump(state, open(path, "w")); sys.exit(code)
def resolve(ref): return tags.get(ref, ref if ref in state["images"] else None)
if args[:2] == ["image", "inspect"]:
    i = resolve(args[-1]); print(i) if i else None; sys.exit(0 if i else 125)
if args[:2] == ["image", "exists"]: sys.exit(0 if args[2] in tags else 1)
if args[0] == "tag":
    i = resolve(args[1])
    if not i: sys.exit(125)
    tags[args[2]] = i; done()
if args[0] == "untag":
    if len(args) == 2: [tags.pop(n) for n in [n for n, v in tags.items() if v == tags.get(args[1])]]  # como el podman real
    else: tags.pop(args[2], None)
    done()
if args[0] == "rmi":
    i = resolve(args[1])
    if i in tags.values(): sys.exit(1)          # sigue con alguna etiqueta
    state["images"].remove(i); done()
sys.exit(2)
'''


@pytest.fixture
def run(tmp_path):
    (tmp_path / "podman").write_text(FAKE_PODMAN)
    (tmp_path / "podman").chmod(0o755)
    func = SCRIPT.read_text().split("promote_candidate() {")[1].split("\n}\n")[0]
    (tmp_path / "fn.sh").write_text(f'IMAGE={IMAGE}\npromote_candidate() {{{func}\n}}\n')

    def go(tags, images):
        (tmp_path / "state.json").write_text(json.dumps({"tags": tags, "images": images}))
        r = subprocess.run(["bash", "-c", f'PATH={tmp_path}:$PATH; . {tmp_path}/fn.sh; promote_candidate'],
                           capture_output=True, text=True)
        state = json.loads((tmp_path / "state.json").read_text())
        return r.returncode, state["tags"], state["images"]
    return go


def test_primera_promocion_sin_latest(run):
    rc, tags, _ = run({f"{IMAGE}:candidate": "A"}, ["A"])
    assert rc == 0 and tags == {f"{IMAGE}:latest": "A"}


def test_segunda_promocion_deja_la_anterior_como_previous(run):
    rc, tags, _ = run({f"{IMAGE}:latest": "A", f"{IMAGE}:candidate": "B"}, ["A", "B"])
    assert rc == 0 and tags == {f"{IMAGE}:latest": "B", f"{IMAGE}:previous": "A"}


def test_tercera_promocion_borra_la_previous_antigua(run):
    rc, tags, images = run({f"{IMAGE}:latest": "B", f"{IMAGE}:previous": "A", f"{IMAGE}:candidate": "C"},
                           ["A", "B", "C"])
    assert rc == 0 and tags == {f"{IMAGE}:latest": "C", f"{IMAGE}:previous": "B"}
    assert "A" not in images


def test_latest_sobrevive_al_limpiar_candidate(run):
    """Regresión: podman untag sin nombre quitaba TODAS las etiquetas de la imagen, incluida latest."""
    _, tags, _ = run({f"{IMAGE}:candidate": "A"}, ["A"])
    assert f"{IMAGE}:latest" in tags and f"{IMAGE}:candidate" not in tags


def test_candidato_identico_a_latest_no_toca_previous(run):
    """Regresión: previous pasaba a ser igual que latest y se borraba la vuelta atrás real."""
    rc, tags, images = run({f"{IMAGE}:latest": "B", f"{IMAGE}:previous": "A", f"{IMAGE}:candidate": "B"},
                           ["A", "B"])
    assert rc == 0 and tags == {f"{IMAGE}:latest": "B", f"{IMAGE}:previous": "A"}
    assert "A" in images
