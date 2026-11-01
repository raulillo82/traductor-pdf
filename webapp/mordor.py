"""Cliente de mordor: ejecuta `health` y `translate` por SSH con la clave dedicada.

El lado de mordor es worker/traductor-ssh (comando forzado): solo acepta esas dos órdenes.
"""
import subprocess


class MordorError(Exception):
    """Fallo al hablar con mordor (red, SSH, apagado)."""


class MordorClient:
    def __init__(self, hosts, user, key, known_hosts, connect_timeout=3):
        self.hosts = list(hosts)
        self.user = user
        self.key = key
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout

    def _base(self, host):
        return ["ssh", "-i", self.key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={self.connect_timeout}",
                "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=yes",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                f"{self.user}@{host}"]

    def find_host(self):
        """Primer host (cable, luego wifi) que responde a `health`; None si ninguno."""
        for host in self.hosts:
            try:
                r = subprocess.run(self._base(host) + ["health"], capture_output=True, text=True,
                                   timeout=self.connect_timeout + 6)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if r.returncode == 0 and r.stdout.strip() == "ok":
                return host
        return None

    def health(self):
        return self.find_host() is not None

    def translate(self, in_path, out_path, on_event, timeout=2100):
        """Traduce in_path -> out_path. on_event(kind, value) recibe las líneas @@ del worker.

        Devuelve (exit_code, mensaje_error|None). Lanza MordorError si no hay conexión.
        """
        host = self.find_host()
        if host is None:
            raise MordorError("mordor no responde")
        error = None
        with open(in_path, "rb") as fin, open(out_path, "wb") as fout:
            p = subprocess.Popen(self._base(host) + ["translate"], stdin=fin, stdout=fout,
                                 stderr=subprocess.PIPE, text=True)
            try:
                for line in p.stderr:
                    line = line.rstrip()
                    if not line.startswith("@@"):
                        continue
                    kind, _, value = line[2:].partition(" ")
                    if kind == "ERROR":
                        error = value
                    on_event(kind, value)
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                raise MordorError("tiempo agotado")
        if p.returncode == 255:
            raise MordorError("conexión SSH perdida")
        return p.returncode, error
