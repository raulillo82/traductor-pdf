#!/bin/bash
# Actualiza la imagen del worker desde la propia máquina del worker, siguiendo origin/main.
#
# Lo lanza un temporizador de systemd (el worker es un portátil: si estaba apagado o sin red, se
# pone al día cuando vuelve). Solo LEE el repositorio público, así que nada de fuera puede
# hacer que ejecute algo. main solo recibe código por pull request con los tests en verde.
#
# Reconstruye solo si cambian worker/, translate_pdf*.py, el glosario o requirements.txt. La imagen
# nueva se construye con la etiqueta "candidate" y se PRUEBA (importaciones y una traducción de
# verdad, sin red) antes de pasar a "latest"; si falla, producción no se toca. La anterior queda como
# "previous" para volver atrás a mano. Un commit que falla se reintenta MAX_ATTEMPTS veces.
#
# Se instala FUERA del repositorio, para que un commit no pueda cambiarlo. Tampoco instala
# traductor-ssh (el comando SSH forzado, que es la frontera de seguridad de la clave de la web):
# si el del repo difiere del instalado, solo avisa; hay que copiarlo a mano tras revisarlo.
set -uo pipefail

REPO=${REPO:-$HOME/.local/share/traductor-worker/repo}
STATE=${STATE:-$HOME/.local/state/traductor-worker-deploy}
IMAGE=${IMAGE:-localhost/traductor-worker}
FORCED=${FORCED:-$HOME/.local/bin/traductor-ssh}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-3}
WATCHED=(worker translate_pdf.py translate_pdf_text.py glosario_ganchillo_es.txt requirements.txt)

log() { echo "[traductor-worker-deploy] $1"; }
RUN=(podman run --rm --network none --cap-drop all --security-opt no-new-privileges)

# Prueba la imagen candidata sin red: dependencias y una traducción de punta a punta
smoke_test() {
    local img=$1 pdf out
    pdf=$(mktemp) out=$(mktemp)
    trap 'rm -f "$pdf" "$out"' RETURN
    "${RUN[@]}" --entrypoint python "$img" -c '
import sys, pymupdf, cv2, PIL, pytesseract, argostranslate.translate
doc = pymupdf.open(); page = doc.new_page()
page.insert_text((72, 72), "Single crochet in the next stitch. Then chain two and slip stitch to join the round.\n"
                           "Fasten off and weave in the ends. Stuff the piece firmly before closing it.")
sys.stdout.buffer.write(doc.tobytes())
' > "$pdf" || return 1
    "${RUN[@]}" -i --tmpfs /tmp:rw,size=1g,mode=1777 -e HOME=/home/app -e XDG_CACHE_HOME=/tmp "$img" < "$pdf" > "$out" 2>/dev/null || return 1
    # El resultado debe ser un PDF con texto traducido (aparece una palabra española del glosario)
    "${RUN[@]}" -i --entrypoint python "$img" -c '
import sys, pymupdf
text = " ".join(p.get_text() for p in pymupdf.open(stream=sys.stdin.buffer.read(), filetype="pdf")).lower()
sys.exit(0 if any(w in text for w in ("punto", "cadena", "cabos", "rellen")) else 1)
' < "$out"
}

# candidate -> latest -> previous. Cuidado: `podman untag IMAGEN` sin nombre quita TODAS las etiquetas de la
# imagen (incluida latest); por eso se pasa siempre el nombre exacto, y al final se comprueba que latest existe.
promote_candidate() {
    local new_id old_previous keep
    new_id=$(podman image inspect -f '{{.Id}}' "$IMAGE:candidate") || return 1
    old_previous=$(podman image inspect -f '{{.Id}}' "$IMAGE:previous" 2> /dev/null || true)
    podman image exists "$IMAGE:latest" && { podman tag "$IMAGE:latest" "$IMAGE:previous" || return 1; }
    podman tag "$new_id" "$IMAGE:latest" || return 1
    podman untag "$IMAGE:candidate" "$IMAGE:candidate" || true
    [ "$(podman image inspect -f '{{.Id}}' "$IMAGE:latest" 2> /dev/null)" = "$new_id" ] || return 1
    # Solo se borra la imagen que deja de ser "previous"; hay otras imágenes ajenas en la máquina
    for keep in latest previous; do
        [ "$old_previous" = "$(podman image inspect -f '{{.Id}}' "$IMAGE:$keep" 2> /dev/null)" ] && old_previous=
    done
    [ -n "$old_previous" ] && podman rmi "$old_previous" > /dev/null 2>&1
    return 0
}

# ¿Hay conexión con lo que necesita un build (registro de imágenes, PyPI y GitHub)? curl sin -f devuelve 0 aunque
# la respuesta sea un 401: solo falla si no hay conexión.
ONLINE_URLS=${ONLINE_URLS:-"https://registry-1.docker.io/v2/ https://pypi.org/simple/ https://raw.githubusercontent.com/"}
online() {
    local url
    for url in $ONLINE_URLS; do
        curl -s -m 8 -o /dev/null "$url" || return 1
    done
}

# Un build o una prueba fallidos solo cuentan como intento si ahora hay red. Sin red, la causa más probable es el
# portátil (wifi caída, cambio de red) y no el código; contarlo agotaría los intentos sin que el commit tenga culpa.
# Un fallo del código con red sí cuenta, así que sigue parándose en MAX_ATTEMPTS: nunca reintenta indefinidamente.
# Devuelve 1 si el fallo cuenta como intento y 0 si no.
record_failure() {
    local target=$1 attempts=$2
    podman rmi "$IMAGE:candidate" > /dev/null 2>&1
    if online; then
        echo "$target $((attempts + 1))" > "$STATE/failed"
        log "FALLO al construir o probar la imagen candidata: producción no se ha tocado"
        return 1
    fi
    log "FALLO sin conexión de red: no cuenta como intento, se reintenta cuando vuelva"
    return 0
}

# Al agotar los intentos el script se queda quieto; se avisa una sola vez por commit (no cada media hora)
exhausted_notice() {
    local target=$1
    [ "$(cat "$STATE/notified" 2> /dev/null)" = "$target" ] && return 0
    echo "$target" > "$STATE/notified"
    log "AVISO: $target agotó $MAX_ATTEMPTS intentos y no se reintentará hasta que llegue otro commit; para forzarlo: rm $STATE/failed"
}

mkdir -p "$STATE"
git -C "$REPO" fetch --quiet origin main || { log "sin red o repositorio no accesible: se reintenta más tarde"; exit 0; }
target=$(git -C "$REPO" rev-parse FETCH_HEAD)
[ -f "$STATE/deployed" ] || git -C "$REPO" rev-parse HEAD > "$STATE/deployed"
deployed=$(cat "$STATE/deployed")
[ "$deployed" = "$target" ] && exit 0

attempts=0
if [ -f "$STATE/failed" ] && [ "$(cut -d' ' -f1 "$STATE/failed")" = "$target" ]; then
    attempts=$(cut -d' ' -f2 "$STATE/failed")
fi
if [ "$attempts" -ge "$MAX_ATTEMPTS" ]; then
    exhausted_notice "$target"
    exit 0
fi

git -C "$REPO" merge --quiet --ff-only "$target" || { log "no se puede avanzar (fast-forward)"; exit 1; }

# El comando forzado no se instala solo: se avisa una vez por commit si difiere del instalado
if [ -f "$FORCED" ] && ! diff -q -I '^#' "$FORCED" "$REPO/worker/traductor-ssh" > /dev/null; then
    log "AVISO: worker/traductor-ssh del repo difiere del instalado en $FORCED: revísalo y cópialo a mano"
fi

if ! git -C "$REPO" diff --quiet "$deployed" "$target" -- "${WATCHED[@]}"; then
    log "construyendo $target, intento $((attempts + 1)) de $MAX_ATTEMPTS"
    if nice -n 19 ionice -c3 podman build -q --pull=newer -f "$REPO/worker/Dockerfile" -t "$IMAGE:candidate" "$REPO" > /dev/null \
            && smoke_test "$IMAGE:candidate"; then
        promote_candidate || { log "FALLO al cambiar las etiquetas: revisa 'podman images'"; exit 1; }
    else
        record_failure "$target" "$attempts"
        exit $?
    fi
fi

echo "$target" > "$STATE/deployed"
rm -f "$STATE/failed"
log "desplegado $target"
