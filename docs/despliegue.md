# Despliegue

Tres piezas: la **web** (un contenedor en un servidor pequeño, detrás de nginx), el **worker**
(otra máquina con más CPU, que hace la traducción) y la **clave SSH** que las une.

```
móvil ──HTTPS──▶ nginx ──▶ contenedor "traductor-web" ──ssh──▶ worker (podman sin red)
```

## Worker (máquina potente)

```
podman build -f worker/Containerfile -t traductor-worker .
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
