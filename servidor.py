"""Predicciones Fútbol en un servidor 24/7 (Linux).

Sirve la app solo dentro del servidor (127.0.0.1:8765). Delante va Caddy, que agrega el candado (HTTPS) y pide
usuario y contraseña: nadie llega a este programa sin pasar antes por Caddy.
Las actualizaciones las lanza el sistema (systemd) cada hora y cada 15 minutos, igual que en la laptop.
"""
import http.server
import json
import socketserver
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from motor import config as C  # noqa: E402

PUERTO = 8765
proceso = None


def leer_json(ruta, defecto):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return defecto


class Manejador(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(C.WEB), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, codigo=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _archivo(self, ruta, tipo):
        try:
            b = Path(ruta).read_bytes()
        except OSError:
            return self._json({"error": "no existe"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta == "/api/ping":
            return self._json({"ok": True, "app": "PrediccionesFutbol"})
        if ruta == "/api/datos":
            return self._archivo(C.ARCHIVO_PREDICCIONES, "application/json; charset=utf-8")
        if ruta == "/api/estado":
            e = leer_json(C.ARCHIVO_ESTADO, {})
            e["proceso_activo"] = bool(proceso and proceso.poll() is None)
            return self._json(e)
        if ruta == "/api/prompt":
            return self._archivo(BASE / "prompt_maestro.txt", "text/plain; charset=utf-8")
        if ruta == "/api/pdf":
            return self._archivo(BASE / "prompt_maestro.pdf", "application/pdf")
        return super().do_GET()

    def do_POST(self):
        global proceso
        ruta = self.path.split("?")[0]
        if ruta == "/api/actualizar":
            if proceso and proceso.poll() is None:
                return self._json({"iniciado": False})
            proceso = subprocess.Popen([sys.executable, str(BASE / "actualizar.py")], cwd=str(BASE),
                                       start_new_session=True)
            return self._json({"iniciado": True})
        if ruta == "/api/abrir_pdf":
            return self._json({"ok": False, "web": True})     # en la web el PDF se abre en otra pestaña
        return self._json({"error": "ruta desconocida"}, 404)


class Servidor(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    Servidor(("127.0.0.1", PUERTO), Manejador).serve_forever()
