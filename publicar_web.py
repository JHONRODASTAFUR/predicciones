"""Arma la página web privada de Predicciones Fútbol (para GitHub Pages).

Cómo funciona, en simple:
  1. Junta en un solo paquete las predicciones, el estado y el prompt maestro.
  2. Lo comprime y lo CIFRA con tu clave (AES-256-GCM; la llave sale de la clave con PBKDF2, 310 000 vueltas).
  3. Copia la app (web/) a la carpeta _sitio/ junto con el paquete cifrado «datos.bin».
  La página es pública en internet, pero sin tu clave solo se ve una pantalla que la pide: los datos no se pueden
  leer. La clave nunca viaja: tu navegador descifra el paquete dentro del celular o la laptop.

Uso:
  CLAVE_PAGINA=... python publicar_web.py                  → arma _sitio/
  CLAVE_PAGINA=... python publicar_web.py --cifrar-prompt  → crea prompt_maestro.enc (el repositorio público
                                                             guarda solo esta versión cifrada del prompt)
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE = Path(__file__).resolve().parent
DATOS = BASE / "datos"
WEB = BASE / "web"
SALIDA = BASE / "_sitio"
VUELTAS = 310_000          # tiene que coincidir con app.js (descifrarPaquete)


def _llave(clave, sal):
    return hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, VUELTAS, 32)


def cifrar(datos, clave):
    sal, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    return sal + iv + AESGCM(_llave(clave, sal)).encrypt(iv, datos, None)


def descifrar(datos, clave):
    sal, iv, ct = datos[:16], datos[16:28], datos[28:]
    return AESGCM(_llave(clave, sal)).decrypt(iv, ct, None)


def leer_prompt(clave):
    txt = BASE / "prompt_maestro.txt"
    if txt.exists():
        return txt.read_text(encoding="utf-8")
    enc = BASE / "prompt_maestro.enc"
    if enc.exists():
        return gzip.decompress(descifrar(enc.read_bytes(), clave)).decode("utf-8")
    return ""


def main():
    clave = os.environ.get("CLAVE_PAGINA", "").strip()     # sin espacios ni saltos de línea invisibles
    if len(clave) < 12:
        sys.exit("Falta CLAVE_PAGINA (o es muy corta)")
    if "--cifrar-prompt" in sys.argv:
        datos = gzip.compress((BASE / "prompt_maestro.txt").read_bytes(), 9)
        (BASE / "prompt_maestro.enc").write_bytes(cifrar(datos, clave))
        print("prompt_maestro.enc creado")
        return
    pred = DATOS / "predicciones.json"
    if not pred.exists():
        sys.exit("Todavía no hay predicciones: no se publica nada")
    datos = json.loads(pred.read_text(encoding="utf-8"))
    try:
        estado = json.loads((DATOS / "estado.json").read_text(encoding="utf-8"))
    except Exception:
        estado = {}
    version = estado.get("ultima_ok") or datos.get("generado") or dt.datetime.now(dt.timezone.utc).isoformat()
    paquete = {"datos": datos, "estado": estado, "prompt": leer_prompt(clave)}
    crudo = gzip.compress(json.dumps(paquete, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)

    shutil.rmtree(SALIDA, ignore_errors=True)
    SALIDA.mkdir()
    for f in WEB.iterdir():
        if f.is_file():
            shutil.copy2(f, SALIDA / f.name)
    v = hashlib.sha1(version.encode()).hexdigest()[:10]
    index = (WEB / "index.html").read_text(encoding="utf-8")
    index = index.replace("<head>", '<head>\n<meta name="robots" content="noindex, nofollow">', 1)
    index = index.replace('<script src="app.js"></script>',
                          f'<script>window.PF_ESTATICO = true;</script>\n<script src="app.js?v={v}"></script>')
    index = index.replace('href="estilos.css"', f'href="estilos.css?v={v}"')
    (SALIDA / "index.html").write_text(index, encoding="utf-8")
    (SALIDA / "datos.bin").write_bytes(cifrar(crudo, clave))
    (SALIDA / "version.txt").write_text(version, encoding="utf-8")
    (SALIDA / ".nojekyll").write_text("", encoding="utf-8")
    (SALIDA / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    print(f"Página armada en {SALIDA}: datos.bin {len(crudo) / 1e6:.1f} MB comprimido, versión {version}")


if __name__ == "__main__":
    main()
