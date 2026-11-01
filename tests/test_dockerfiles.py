"""Las imágenes base se fijan por digest del índice multiarquitectura, y Dependabot propone el nuevo."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILES = sorted(p for p in ROOT.glob("*/Dockerfile"))


def test_hay_dockerfiles():
    assert {p.parent.name for p in DOCKERFILES} >= {"webapp", "worker"}


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
def test_base_fijada_por_digest(path):
    froms = re.findall(r"(?m)^FROM\s+(\S+)", path.read_text())
    assert froms, f"{path} no tiene FROM"
    for image in froms:
        assert re.fullmatch(r"\S+:\S+@sha256:[0-9a-f]{64}", image), (
            f"{path.parent.name}: {image} debe ir como imagen:tag@sha256:<digest del índice multiarquitectura>")


def test_dependabot_vigila_cada_dockerfile():
    config = (ROOT / ".github/dependabot.yml").read_text()
    for path in DOCKERFILES:
        assert re.search(rf"package-ecosystem: docker\s+directory: /{path.parent.name}\b", config), path
