# Base fijada por digest del ÍNDICE multiarquitectura (no el de una sola arquitectura): así lo que corre es lo
# que revisó una pull request. Dependabot propone el nuevo digest cada mes. Se construye en amd64 y arm64.
FROM docker.io/library/python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app

WORKDIR /srv
COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY webapp ./webapp

USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 5002
# Un solo proceso: la cola de trabajos vive en un hilo de este proceso.
CMD ["gunicorn", "-b", "0.0.0.0:5002", "--workers", "1", "--threads", "8", "--timeout", "120", "--access-logfile", "-", "--access-logformat", "%(h)s %(m)s %(U)s %(s)s", "webapp:create_app()"]
