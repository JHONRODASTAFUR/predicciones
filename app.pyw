"""Predicciones Fútbol: abre la aplicación en su propia ventana.

Doble clic en el acceso directo del escritorio. No necesita a Claude ni ninguna cuenta.
"""
import datetime as dt
import http.server
import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from motor import config as C  # noqa: E402

PUERTO = 8765
APAGAR_TRAS_S = 600            # se cierra solo 10 min después de cerrar la ventana
EDAD_MAX_DATOS_MIN = 60        # al abrir, si los datos tienen más de 1 h se actualizan
logging.basicConfig(filename=str(C.LOGS / "app.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")

ultimo_ping = time.time()
proceso = None
candado_proc = threading.Lock()


def leer_json(ruta, defecto):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return defecto


def interprete_sin_ventana():
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def lanzar_actualizacion(forzar=False):
    global proceso
    with candado_proc:
        if proceso and proceso.poll() is None:
            return False
        args = [interprete_sin_ventana(), str(BASE / "actualizar.py")] + (["--forzar"] if forzar else [])
        proceso = subprocess.Popen(args, cwd=str(BASE), creationflags=0x08000000)
        return True


def datos_viejos():
    e = leer_json(C.ARCHIVO_ESTADO, {})
    ult = e.get("ultima_ok")
    if not ult or not C.ARCHIVO_PREDICCIONES.exists():
        return True
    try:
        return dt.datetime.now().astimezone() - dt.datetime.fromisoformat(ult) > dt.timedelta(minutes=EDAD_MAX_DATOS_MIN)
    except ValueError:
        return True


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
        global ultimo_ping
        ruta = self.path.split("?")[0]
        if ruta == "/api/ping":
            ultimo_ping = time.time()
            return self._json({"ok": True, "app": "PrediccionesFutbol"})
        if ruta == "/api/datos":
            return self._archivo(C.ARCHIVO_PREDICCIONES, "application/json; charset=utf-8")
        if ruta == "/api/estado":
            e = leer_json(C.ARCHIVO_ESTADO, {})
            e["proceso_activo"] = bool(proceso and proceso.poll() is None)
            return self._json(e)
        if ruta == "/api/prompt":
            return self._archivo(BASE / "prompt_maestro.txt", "text/plain; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        ruta = self.path.split("?")[0]
        if ruta == "/api/actualizar":
            return self._json({"iniciado": lanzar_actualizacion("forzar" in self.path)})
        if ruta == "/api/abrir_pdf":
            try:
                os.startfile(str(BASE / "prompt_maestro.pdf"))
                return self._json({"ok": True})
            except OSError as ex:
                return self._json({"ok": False, "error": str(ex)})
        return self._json({"error": "ruta desconocida"}, 404)


class Servidor(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def ya_abierta(puerto):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=2) as r:
            return json.load(r).get("app") == "PrediccionesFutbol"
    except Exception:
        return False


def abrir_ventana(url):
    candidatos = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for exe in candidatos:
        if exe.exists():
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1440,920", "--no-first-run",
                              "--no-default-browser-check"], creationflags=0x08000000)
            return
    webbrowser.open(url)


def vigilante(servidor):
    while True:
        time.sleep(20)
        if time.time() - ultimo_ping > APAGAR_TRAS_S and not (proceso and proceso.poll() is None):
            logging.info("Ventana cerrada hace rato: se apaga el servidor")
            servidor.shutdown()
            return


def main():
    global ultimo_ping
    if ya_abierta(PUERTO):
        abrir_ventana(f"http://127.0.0.1:{PUERTO}/")
        return
    servidor = None
    for p in range(PUERTO, PUERTO + 20):
        try:
            servidor = Servidor(("127.0.0.1", p), Manejador)
            break
        except OSError:
            continue
    if servidor is None:
        logging.error("No hay puerto libre")
        return
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    logging.info("App abierta en %s", url)
    if datos_viejos():
        lanzar_actualizacion()
    ultimo_ping = time.time()
    threading.Thread(target=vigilante, args=(servidor,), daemon=True).start()
    abrir_ventana(url)
    servidor.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Fallo de la app")
