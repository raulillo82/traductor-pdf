# Base fijada por digest del ÍNDICE multiarquitectura (no el de una sola arquitectura): así lo que corre es lo
# que revisó una pull request. Dependabot propone el nuevo digest cada mes. Se construye en amd64 y arm64.
FROM docker.io/library/python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-eng fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El modelo de traducción se instala al construir: en ejecución no hay red.
USER app
RUN python -c "import argostranslate.package as p; p.update_package_index(); \
a=[x for x in p.get_available_packages() if x.from_code=='en' and x.to_code=='es'][0]; \
p.install_from_path(a.download())"

# Calentamiento: Stanza (división en frases) descarga recursos en el primer uso; que queden en la imagen.
RUN python -c "import argostranslate.translate as t; print(t.translate('Single crochet in the next stitch. Then chain 2.','en','es'))"

COPY --chown=app translate_pdf.py translate_pdf_text.py glosario_ganchillo_es.txt ./
COPY --chown=app worker/traducir.py ./
ENTRYPOINT ["python", "/app/traducir.py"]
