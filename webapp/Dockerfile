FROM docker.io/library/python:3.15-slim

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
