#!/bin/bash
# Prueba de una imagen del worker, sin red: importa las dependencias, genera un PDF y lo traduce de punta a punta.
# Uso: worker/smoke_test.sh IMAGEN        (RUNTIME=docker para usar docker en lugar de podman)
# Sale con 0 si la imagen sirve. La usa el CI; worker-deploy.sh lleva su propia copia (se instala fuera del repo).
set -uo pipefail
IMG=${1:?Uso: worker/smoke_test.sh IMAGEN}
RT=${RUNTIME:-podman}
RUN=("$RT" run --rm --network none --cap-drop all --security-opt no-new-privileges)

pdf=$(mktemp) out=$(mktemp)
trap 'rm -f "$pdf" "$out"' EXIT

"${RUN[@]}" --entrypoint python "$IMG" -c '
import sys, pymupdf, cv2, PIL, pytesseract, argostranslate.translate
doc = pymupdf.open(); page = doc.new_page()
page.insert_text((72, 72), "Single crochet in the next stitch. Then chain two and slip stitch to join the round.\n"
                           "Fasten off and weave in the ends. Stuff the piece firmly before closing it.")
sys.stdout.buffer.write(doc.tobytes())
' > "$pdf" || { echo "FALLO: no se pudo generar el PDF de prueba" >&2; exit 1; }

"${RUN[@]}" -i --tmpfs /tmp:rw,size=1g,mode=1777 -e HOME=/home/app -e XDG_CACHE_HOME=/tmp "$IMG" < "$pdf" > "$out" \
    || { echo "FALLO: el worker no tradujo el PDF de prueba" >&2; exit 1; }

# El resultado debe ser un PDF con texto traducido (aparece una palabra española del glosario)
"${RUN[@]}" -i --entrypoint python "$IMG" -c '
import sys, pymupdf
text = " ".join(p.get_text() for p in pymupdf.open(stream=sys.stdin.buffer.read(), filetype="pdf")).lower()
sys.exit(0 if any(w in text for w in ("punto", "cadena", "cabos", "rellen")) else 1)
' < "$out" || { echo "FALLO: el PDF de salida no contiene texto traducido" >&2; exit 1; }
echo "OK: $IMG traduce de punta a punta"
