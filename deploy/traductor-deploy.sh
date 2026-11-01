#!/bin/bash
# Despliega la web desde el propio servidor, siguiendo origin/main.
#
# Lo lanza un temporizador de systemd, así que nada de fuera puede hacer que ejecute
# algo: solo lee el repositorio público. main solo recibe código por pull request con
# los tests en verde.
#
# Si la versión nueva no responde, vuelve a la imagen anterior. Un commit que falla se
# reintenta MAX_ATTEMPTS veces y luego se deja en paz hasta que llegue otro. Si hay una
# traducción en curso o en cola, espera al siguiente aviso del temporizador (reiniciar
# la web la empezaría de cero).
#
# Se instala FUERA del repositorio, para que un commit no pueda cambiar este script:
# para actualizarlo, cópialo a mano a ~/.local/bin.
# No toca el worker (otra máquina) ni su comando SSH forzado.
set -uo pipefail

REPO=${REPO:-$HOME/traductor/repo}
STATE=${STATE:-$HOME/.local/state/traductor-deploy}
UNITS=${UNITS:-$HOME/.config/systemd/user}
IMAGE=${IMAGE:-localhost/traductor-web}
CONTAINER=${CONTAINER:-traductor}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:5002/healthz}
HEALTH_TRIES=${HEALTH_TRIES:-15}
HEALTH_WAIT=${HEALTH_WAIT:-2}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-3}

log() { echo "[traductor-deploy] $1"; }

healthy() {
    local try code
    for ((try = 0; try < HEALTH_TRIES; try++)); do
        code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$HEALTH_URL")
        [ "$code" = 200 ] && return 0
        sleep "$HEALTH_WAIT"
    done
    return 1
}

# Número de trabajos en curso o en cola; 0 si el contenedor no responde (no hay nada que perder)
busy_jobs() {
    podman exec "$CONTAINER" python -c '
import sqlite3
c = sqlite3.connect("file:/data/app.db?mode=ro", uri=True)
print(c.execute("SELECT COUNT(*) FROM jobs WHERE status IN (\"queued\", \"running\")").fetchone()[0])
' 2>/dev/null || echo 0
}

# Las etiquetas solo se mueven cuando la imagen nueva se ha construido
restart_new_version() {
    podman tag "$IMAGE:latest" "$IMAGE:previous" || return 1
    podman build -q -f "$REPO/webapp/Dockerfile" -t "$IMAGE:latest" "$REPO" || return 1
    systemctl --user restart traductor.service && healthy
}

roll_back() {
    podman tag "$IMAGE:previous" "$IMAGE:latest" && systemctl --user restart traductor.service
}

mkdir -p "$STATE"
git -C "$REPO" fetch --quiet origin main || { log "no se puede leer origin/main"; exit 1; }
target=$(git -C "$REPO" rev-parse FETCH_HEAD)
[ -f "$STATE/deployed" ] || git -C "$REPO" rev-parse HEAD > "$STATE/deployed"
deployed=$(cat "$STATE/deployed")
[ "$deployed" = "$target" ] && exit 0

attempts=0
if [ -f "$STATE/failed" ] && [ "$(cut -d' ' -f1 "$STATE/failed")" = "$target" ]; then
    attempts=$(cut -d' ' -f2 "$STATE/failed")
fi
[ "$attempts" -ge "$MAX_ATTEMPTS" ] && exit 0

git -C "$REPO" merge --quiet --ff-only "$target" || { log "no se puede avanzar (fast-forward)"; exit 1; }

# Solo lo que entra en la imagen o en el servicio exige reconstruir y reiniciar
if ! git -C "$REPO" diff --quiet "$deployed" "$target" -- webapp deploy/traductor.service; then
    if [ "$(busy_jobs)" != 0 ]; then
        log "hay una traducción en curso o en cola: se aplaza $target"
        exit 0
    fi
    echo "$target $((attempts + 1))" > "$STATE/failed"
    log "desplegando $target, intento $((attempts + 1)) de $MAX_ATTEMPTS"
    install -m 644 "$REPO"/deploy/traductor.service "$UNITS"/
    systemctl --user daemon-reload
    if ! restart_new_version; then
        log "FALLO: se vuelve a la versión anterior"
        roll_back
        exit 1
    fi
fi

echo "$target" > "$STATE/deployed"
rm -f "$STATE/failed"
# Cada build deja sin etiqueta la imagen anterior de la misma etiqueta
podman image prune -f > /dev/null
log "desplegado $target"
