"""Notificaciones push web (VAPID). Las claves se generan en el primer arranque."""
import base64
import json
import logging
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

log = logging.getLogger(__name__)


def default_subject(base_url):
    """`sub` del VAPID: py_vapid solo admite `mailto:` o `https://dominio` SIN ruta."""
    u = urlsplit(base_url)
    return f"{u.scheme}://{u.netloc}"


def ensure_vapid(pem_path):
    """Devuelve la clave pública (base64url, sin relleno) y crea el PEM privado si falta."""
    v = Vapid()
    try:
        v = Vapid.from_file(str(pem_path))
    except Exception:
        v.generate_keys()
        v.save_key(str(pem_path))
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def send(sub, payload, pem_path, subject, webpush_fn=None):
    """Envía un push. Devuelve False si la suscripción ya no existe (404/410)."""
    if webpush_fn is None:
        from pywebpush import webpush as webpush_fn
    from pywebpush import WebPushException
    try:
        webpush_fn(subscription_info={"endpoint": sub["endpoint"],
                                      "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                   data=json.dumps(payload), vapid_private_key=str(pem_path),
                   vapid_claims={"sub": subject}, ttl=86400)
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return False
        log.warning("push falló (%s): %s", status, e)
        return True
    except Exception as e:  # red caída, etc.: no es motivo para borrar la suscripción
        log.warning("push falló: %s", e)
        return True
