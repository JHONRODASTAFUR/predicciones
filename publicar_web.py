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


HORAS_CERCA = 72          # los partidos que empiezan después de este plazo viajan «livianos»
PESADOS = ("extras", "matriz", "noticias", "necesidad", "momentos", "cambios", "disciplina", "clima")


def aligerar(datos, ahora=None):
    """Para que la página abra rápido: a los partidos lejanos se les quita el detalle pesado, que va aparte.

    Devuelve {id: detalle}. La app descarga ese segundo archivo (lejos.bin) solo cuando abres uno de esos partidos.
    En la lista y el panel siguen viéndose igual: probabilidades, las 3 apuestas, clase de riesgo y veredicto."""
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    lejos = {}
    for m in datos.get("partidos", []):
        if m.get("estado") != "pre":
            continue
        inicio = dt.datetime.fromisoformat(m["fecha"].replace("Z", "+00:00"))
        if (inicio - ahora).total_seconds() <= HORAS_CERCA * 3600:
            continue
        p = {k: m.pop(k) for k in PESADOS if m.get(k) is not None}
        ap = m.get("apuesta")
        if ap and ap.get("mercados"):
            usados = {x["cod"] for x in (ap.get("perfiles") or {}).values()}
            p["mercados"] = ap["mercados"]
            ap["mercados"] = [f for f in ap["mercados"] if f["cod"] in usados]
        if m.get("alineacion"):
            p["alineacion"] = m["alineacion"]
            m["alineacion"] = {"estado": m["alineacion"].get("estado")}
        if m.get("sim"):
            p["sim"] = m["sim"]
            m["sim"] = {"n": m["sim"]["n"], "estabilidad": m["sim"]["estabilidad"]}
        if m.get("intel"):
            it = p["intel"] = m["intel"]
            m["intel"] = {"veredicto": it["veredicto"], "puntaje": it.get("puntaje"), "discrepancias": [], "riesgos": [],
                          "perfiles": {k: {q: x.get(q) for q in ("cod", "clase", "evidencia", "motivo")}
                                       for k, x in (it.get("perfiles") or {}).items()}}
        if m.get("ctx"):
            c = p["ctx"] = m["ctx"]
            m["ctx"] = {s: {"forma": (c.get(s) or {}).get("forma")} for s in ("h", "a")}
        if (m.get("pred") or {}).get("calc") is not None:
            p["calc"] = m["pred"].pop("calc")
        m["_lejos"] = True
        lejos[m["id"]] = p
    return lejos


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
    lejos = aligerar(datos)
    # «repo» (dueño/proyecto, lo da GitHub) le dice al botón «Actualizar ahora» a qué flujo llamar; no es secreto
    paquete = {"datos": datos, "estado": estado, "prompt": leer_prompt(clave),
               "repo": os.environ.get("GITHUB_REPOSITORY", "")}
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
    index = index.replace('<script src="panel.js"></script>', f'<script src="panel.js?v={v}"></script>')
    index = index.replace('<script src="auditoria.js"></script>', f'<script src="auditoria.js?v={v}"></script>')
    index = index.replace('href="estilos.css"', f'href="estilos.css?v={v}"')
    (SALIDA / "index.html").write_text(index, encoding="utf-8")
    (SALIDA / "datos.bin").write_bytes(cifrar(crudo, clave))
    crudo_lejos = gzip.compress(json.dumps(lejos, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    (SALIDA / "lejos.bin").write_bytes(cifrar(crudo_lejos, clave))
    print(f"Detalle de {len(lejos)} partidos lejanos aparte: lejos.bin {len(crudo_lejos) / 1e6:.1f} MB comprimido")
    (SALIDA / "version.txt").write_text(version, encoding="utf-8")
    (SALIDA / ".nojekyll").write_text("", encoding="utf-8")
    (SALIDA / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    print(f"Página armada en {SALIDA}: datos.bin {len(crudo) / 1e6:.1f} MB comprimido, versión {version}")


if __name__ == "__main__":
    main()
