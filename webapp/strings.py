"""Textos de la interfaz. Solo español por ahora; añadir otro idioma = otro diccionario."""

ES = {
    "app.title": "Traductor de patrones",
    "login.intro": "Traduce patrones en PDF del inglés al español.",
    "login.google": "Entrar con Google",
    "login.closed": "Registro de nuevos usuarios desactivado. Si ya tienes cuenta, inicia sesión con Google.",
    "msg.pending": "Tu cuenta está pendiente de aprobación. Cuando se apruebe podrás entrar.",
    "msg.blocked": "Esta cuenta no tiene acceso.",
    "msg.closed": "Registro de nuevos usuarios desactivado. Web no disponible para cuentas nuevas.",
    "msg.error": "No se pudo iniciar sesión con Google. Inténtalo de nuevo.",
    "msg.unverified": "Tu cuenta de Google no tiene el correo verificado.",
    "status.up": "Servicio disponible",
    "status.down": "Servicio no disponible: el ordenador de Raúl está apagado o fuera de casa en este momento. Inténtalo más tarde.",
    "err.too_big": "El PDF supera el tamaño máximo de {max_mb} MB.",
    "err.not_pdf": "El fichero no es un PDF.",
    "err.no_file": "Elige un PDF.",
    "err.busy": "Ya tienes una traducción en curso. Espera a que termine.",
    "err.daily": "Has llegado al máximo de {n} traducciones por día. Inténtalo mañana.",
    "err.down": "Servicio no disponible: el ordenador de Raúl está apagado o fuera de casa en este momento. Inténtalo más tarde.",
    "job.queued": "En cola…",
    "job.running": "Traduciendo…",
    "job.done": "Listo",
    "job.error": "Ha fallado",
    "job.lost": "Se perdió la conexión con el ordenador de Raúl durante la traducción. Vuelve a intentarlo.",
    "job.failed": "No se pudo traducir el PDF.",
    "job.timeout": "La traducción tardó demasiado y se canceló.",
    "push.title": "Traducción lista",
    "push.body": "{name} ya está traducido.",
    "push.title_err": "La traducción ha fallado",
}


def t(key, **kw):
    s = ES.get(key, key)
    return s.format(**kw) if kw else s
