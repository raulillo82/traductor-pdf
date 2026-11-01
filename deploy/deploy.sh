#!/bin/bash
# Despliega el HEAD actual en el servidor: construye la imagen allí y reinicia el servicio.
# Uso: deploy/deploy.sh [host-ssh]
# El host sale del argumento, de $TRADUCTOR_HOST o de deploy/local.env (ignorado por git).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ -f deploy/local.env ] && . deploy/local.env
HOST="${1:-${TRADUCTOR_HOST:-}}"
[ -n "$HOST" ] || { echo "Indica el host SSH: deploy/deploy.sh <host>, TRADUCTOR_HOST o deploy/local.env" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || echo "Aviso: hay cambios sin commitear; se despliega solo HEAD." >&2

ssh "$HOST" 'mkdir -p ~/traductor/data ~/traductor/secrets ~/traductor/src && chmod 700 ~/traductor/secrets'
ssh "$HOST" 'test -f ~/traductor/traductor.env' || { echo "Falta ~/traductor/traductor.env en $HOST (ver deploy/traductor.env.example)" >&2; exit 1; }

git archive --format=tar HEAD | ssh "$HOST" 'rm -rf ~/traductor/src/* && tar -x -C ~/traductor/src'
ssh "$HOST" 'cd ~/traductor/src \
  && podman build -q -f webapp/Dockerfile -t traductor-web . \
  && mkdir -p ~/.config/systemd/user && cp deploy/traductor.service ~/.config/systemd/user/ \
  && systemctl --user daemon-reload && systemctl --user enable traductor \
  && systemctl --user restart traductor'
sleep 4
ssh "$HOST" 'curl -fsS http://127.0.0.1:5002/healthz && echo " <- traductor responde"'
