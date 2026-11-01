# Despliegue

Tres piezas: la **web** (un contenedor en un servidor pequeño, detrás de nginx), el **worker**
(otra máquina con más CPU, que hace la traducción) y la **clave SSH** que las une.

```
móvil ──HTTPS──▶ nginx ──▶ contenedor "traductor-web" ──ssh──▶ worker (podman sin red)
```

## Worker (máquina potente)

```
podman build -f worker/Dockerfile -t traductor-worker .
install -m 755 worker/traductor-ssh ~/.local/bin/traductor-ssh
```

En el servidor web, una clave dedicada (`ssh-keygen -t ed25519 -f ~/traductor/secrets/traductor_mordor`),
y en el `~/.ssh/authorized_keys` del worker una línea que la limite a la IP del servidor web y al
comando forzado:

```
from="<IP del servidor web>",command="/home/<usuario>/.local/bin/traductor-ssh",restrict ssh-ed25519 AAAA…
```

El comando forzado solo acepta `health` y `translate`. La traducción corre en un contenedor sin red,
sin capacidades y sin privilegios, de un trabajo en uno. Fijar las huellas del worker en
`secrets/known_hosts` (`ssh-keyscan -t ed25519 <ip>` y comparar con `ssh-keygen -lf` en el worker).

## Web (servidor)

1. Crear `~/traductor/traductor.env` a partir de `deploy/traductor.env.example`.
2. Crear en `~/traductor/secrets/` (permisos 600): `traductor_mordor`, `known_hosts`,
   `google_client_id` y `google_client_secret`.
3. `deploy/deploy.sh <host-ssh>` construye la imagen en el servidor y reinicia el servicio systemd de usuario.

### Cliente OAuth de Google

En Google Cloud Console: crear un proyecto, configurar la pantalla de consentimiento (usuarios externos,
ámbitos `openid`, `email`, `profile`) y crear un ID de cliente "Aplicación web" con esta URI de redirección:

```
https://<tu-dominio>/traductor/auth/callback
```

### nginx

Dentro del bloque `server` de 443:

```nginx
location = /traductor { return 302 /traductor/; }
location /traductor/ {
    client_max_body_size 100M;     # el límite real lo aplica la app (MAX_MB)
    proxy_pass         http://127.0.0.1:5002/;
    proxy_http_version 1.1;
    proxy_set_header   Connection "";
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_set_header   X-Forwarded-Prefix /traductor;
    proxy_read_timeout 600;
    proxy_send_timeout 600;
    proxy_request_buffering off;
}
```

La app confía en las cabeceras `X-Forwarded-*`: el puerto 5002 debe escuchar solo en `127.0.0.1`.

## Acceso

- `ADMIN_EMAILS`: administradores (aprobados siempre, ven `/admin`).
- `PRE_APPROVED_EMAILS`: cuentas aprobadas de antemano, entran aunque el registro esté cerrado.
- El registro de nuevas cuentas se abre y cierra desde `/admin`. Cerrado por defecto.

## Despliegue automático de la web

Como alternativa a `deploy/deploy.sh` (que empuja el código desde tu máquina), el propio servidor puede seguir
`main` con un temporizador, sin runner de Actions ni credenciales (el repositorio es público):

```
git clone https://github.com/raulillo82/traductor-pdf.git ~/traductor/repo
install -m 755 ~/traductor/repo/deploy/traductor-deploy.sh ~/.local/bin/
install -m 644 ~/traductor/repo/deploy/traductor-deploy.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now traductor-deploy.timer
```

Cada 5 minutos mira `origin/main`. Si ha cambiado `webapp/` o el unit del servicio, reconstruye la imagen y
reinicia; si la web no responde en `/healthz`, vuelve a la imagen anterior (`traductor-web:previous`). Un commit
que falla se reintenta 3 veces y se deja hasta que llegue otro. Si hay una traducción en curso o en cola, aplaza
el reinicio al siguiente aviso. Estado en `~/.local/state/traductor-deploy/`.

El script se instala **fuera del repositorio** a propósito: un commit no puede cambiarlo; para actualizarlo,
vuelve a copiarlo a mano. No toca el worker ni `traductor-ssh`.

## Actualización automática del worker

El worker suele ser un portátil: puede estar apagado o en otra red. Por eso **tira** él mismo del repo público
(la web no puede empujar nada hacia él: su clave SSH solo admite `health` y `translate`), y se pone al día
cuando vuelve a estar encendido.

```
git clone https://github.com/raulillo82/traductor-pdf.git ~/.local/share/traductor-worker/repo
install -m 755 ~/.local/share/traductor-worker/repo/deploy/worker-deploy.sh ~/.local/bin/traductor-worker-deploy.sh
install -m 644 ~/.local/share/traductor-worker/repo/deploy/worker-deploy.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now worker-deploy.timer
```

Reconstruye solo si cambian `worker/`, `translate_pdf*.py`, el glosario o `requirements.txt`. La imagen nueva
se construye como `candidate` (con `nice`/`ionice`, para no molestar) y se **prueba antes de usarla**: importa
las dependencias y traduce de punta a punta un PDF generado al momento, sin red. Solo si pasa se etiqueta como
`latest` (la anterior queda como `previous`, para volver atrás a mano con `podman tag`). Si falla, producción
no se toca; cada commit se reintenta 3 veces. No borra imágenes ajenas, solo la que deja de ser `previous`.

Igual que en la web, el script se instala fuera del repositorio a propósito. Tampoco instala `traductor-ssh`
(el comando forzado, la frontera de seguridad de la clave de la web): si el del repo difiere del instalado,
solo avisa en el log (`journalctl --user -u worker-deploy`); cópialo a mano tras revisarlo.

El glosario vivo (`~/.config/traductor/glosario.txt`) no se toca.
