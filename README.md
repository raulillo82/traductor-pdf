# traductor-pdf
Traductor local de PDFs (EN→ES): OCR + Argos, sin APIs externas

Más detalle: [`docs/despliegue.md`](docs/despliegue.md) (instalación), [`docs/glosario.md`](docs/glosario.md)
(formato del glosario) y [`RESUMEN_translate_pdf.md`](RESUMEN_translate_pdf.md) (cómo funciona el traductor).

## Licencia

El código de este repositorio es [MIT](LICENSE).

**Nota sobre PyMuPDF:** `translate_pdf_text.py` usa [PyMuPDF](https://pymupdf.readthedocs.io/), que se
distribuye bajo AGPL-3.0 (o licencia comercial de Artifex). Mis ficheros siguen siendo MIT, pero quien
redistribuya o ofrezca como servicio de red el conjunto junto con PyMuPDF debe cumplir también la AGPL
de esa dependencia (en la práctica, ofrecer el código fuente completo a sus usuarios).
