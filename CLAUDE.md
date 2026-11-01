# traductor-pdf

Traductor local de PDFs EN→ES (OCR + Argos, sin APIs externas) con una web Flask para usarlo desde el
móvil. **Repositorio público**: todo lo que se commitee queda a la vista de todos, historial incluido.

## Piezas

- `translate_pdf.py` (PDF de imágenes: OCR) y `translate_pdf_text.py` (PDF con texto): el traductor.
- `glosario_ganchillo_es.txt`: glosario de ganchillo. Formato en `docs/glosario.md`.
- `webapp/`: la web (login de Google con aprobación manual, cola de un trabajo, avisos push). Contenedor.
- `worker/`: contenedor sin red que traduce y `traductor-ssh`, el comando SSH forzado (solo `health` y `translate`).
- `deploy/`, `docs/despliegue.md`: despliegue. La web llama al worker por SSH con una clave dedicada.

## Comandos

```
pip install -r webapp/requirements.txt -r webapp/requirements-dev.txt ruff==0.17.3
python -m pytest tests -q        # sin cv2/pymupdf/argostranslate se saltan los tests que los necesitan
ruff check .                     # E, F, I; E501 desactivado (pyproject.toml)
```

El CI (`.github/workflows/tests.yml`) instala todo salvo `argostranslate`, que es enorme: solo se salta
`tests/scripts/test_text_drawings.py`. Si cambias el glosario o `translate_pdf*.py`, corre los tests del
glosario con `cv2` y `pymupdf` instalados, o se saltarán en silencio.

## Flujo de trabajo

- `main` está protegida: **nada de push directo**. Rama + PR, con el check `tests` en verde. Squash merge por defecto.
- Commits firmados con GPG. Autor/committer: `17827278+raulillo82@users.noreply.github.com`, no el Gmail.
- Añade tests para lo que cambies cuando tenga sentido.
- Dependabot abre PR semanales (pip, docker, github-actions). Las subidas mayores (Python, PyMuPDF, Pillow) se
  prueban con una traducción real antes de fusionar: el CI no construye las imágenes ni traduce PDFs de verdad.

## Cosas a tener en cuenta

- **Nada de infraestructura ni datos personales en git**: ni hostnames, IPs, usuarios, correos reales,
  ni nombres de terceros, ni PDFs de patrones (derechos de terceros; `*.pdf` está en `.gitignore`).
  Esos valores salen de variables de entorno o de ficheros ignorados (`deploy/local.env`, `secrets/`).
- Los workflows de Actions van siempre en `ubuntu-latest` con `permissions: contents: read`. **Nunca en un
  runner propio**: el repo es público y un PR ajeno ejecutaría código en un servidor propio.
- El worker usa `~/.config/traductor/glosario.txt` si existe (se edita sin reconstruir la imagen). Es la copia
  viva del glosario: las mejoras que valgan para todos hay que traerlas a `glosario_ganchillo_es.txt` en el repo.
- `PyMuPDF` es AGPL: el README explica el efecto sobre la licencia MIT del resto.
- La web procesa PDF subidos por usuarios y lanza trabajo por SSH: cualquier cambio en `webapp/` o en
  `worker/traductor-ssh` merece pensar en seguridad (entradas externas, permisos, límites de tamaño y páginas).
