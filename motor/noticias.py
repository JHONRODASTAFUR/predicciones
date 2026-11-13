"""Noticias de cada equipo (lesiones, sanciones, dudas, entrenador) desde Google Noticias (RSS gratuito).

Cómo funciona, en simple:
  - Para los equipos que juegan en las próximas 48 horas, se busca en Google Noticias (que junta Marca, AS,
    ESPN, Ovación, Depor, Líbero, BBC...) los titulares de los últimos 5 días con palabras como «lesión»,
    «baja», «sancionado», «duda», «convocatoria».
  - Cada equipo se vuelve a buscar como máximo cada 3 horas (para no abusar del servicio) y los resultados se
    guardan en datos/noticias.json.
  - Cada titular se clasifica (lesión, sanción, duda, entrenador, convocatoria) y, si nombra a un titular
    habitual del equipo, ese jugador se marca como «posible baja».
  - Las noticias se MUESTRAN y permiten simular un escenario, pero NO cambian solas la predicción principal:
    un titular no se puede comprobar con partidos pasados (R19).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .config import DATOS, USER_AGENT

ARCHIVO = DATOS / "noticias.json"
URL = "https://news.google.com/rss/search?q={q}&hl=es-419&gl=PE&ceid=PE:es-419"
PALABRAS = ("lesión", "lesionado", "baja", "sancionado", "suspendido", "duda", "convocatoria", "alineación",
            "injury", "injured", "suspended")
REFRESCO_H = 3          # horas entre búsquedas del mismo equipo
VENTANA_H = 48          # equipos que juegan en las próximas horas
MAX_BUSQUEDAS = 150     # por actualización
DIAS = 5                # antigüedad máxima de las noticias

TIPOS = [   # (tipo, expresión): el primero que coincide manda
    ("sancion", r"sancion|suspendid|expulsad|tarjeta roja|acumulaci[oó]n de amarillas|suspension|\bban\b|banned"),
    ("lesion", r"lesi[oó]n|lesionad|rotura|desgarro|esguince|molestias|parte m[eé]dico|quir[oó]fano|operad|"
               r"injur|hamstring|knock|sidelined"),
    ("duda", r"\bduda\b|en duda|no jugar[aá]|se pierde|se perder[aá]|no estar[aá]|descartad|\bbaja\b|bajas|"
             r"ausencia|ausente|doubt|ruled out|will miss|\bout\b"),
    ("dt", r"entrenador|t[eé]cnico|\bdt\b|destituid|despedid|cesad|interino|manager|head coach|sacked"),
    ("convocatoria", r"convocatoria|convocados|alineaci[oó]n|once inicial|titular|lineup|line-up|squad"),
]
TIPOS_BAJA = {"sancion", "lesion", "duda"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def clasificar(titulo):
    t = titulo.lower()
    for tipo, rx in TIPOS:
        if re.search(rx, t):
            return tipo
    return None


def _buscar(nombre):
    q = (f'"{nombre}" (fútbol OR futbol OR football OR soccer OR gol OR DT) ('
         + " OR ".join(f'"{p}"' if " " in p else p for p in PALABRAS)
         + f") -baloncesto -basket -básquet -euroliga -acb -nba -vóley -voleibol when:{DIAS}d")
    req = urllib.request.Request(URL.format(q=urllib.parse.quote(q)), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        raiz = ET.fromstring(r.read())
    out, vistos = [], set()
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        fuente = (it.findtext("source") or "").strip()
        if fuente and titulo.endswith(" - " + fuente):
            titulo = titulo[: -len(fuente) - 3].strip()
        tipo = clasificar(titulo)
        clave = _norm(titulo)[:80]
        if not tipo or clave in vistos:
            continue
        vistos.add(clave)
        try:
            f = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(dt.timezone.utc)
        except Exception:
            continue                                   # sin fecha no se puede saber si es actual
        if f < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=DIAS):
            continue                                   # Google a veces devuelve notas viejas
        fecha = f.isoformat()
        out.append({"titulo": titulo, "fuente": fuente, "link": (it.findtext("link") or "").strip(),
                    "fecha": fecha, "tipo": tipo})
    out.sort(key=lambda x: x["fecha"] or "", reverse=True)
    return out[:10]


def cargar():
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sincronizar(proximos, prioridad=lambda e: 0):
    """Busca noticias de los equipos que juegan pronto. Devuelve {id_equipo: {"t": iso, "items": [...]}}."""
    cache = cargar()
    ahora = dt.datetime.now(dt.timezone.utc)
    limite = ahora - dt.timedelta(days=DIAS + 2)
    cache = {k: v for k, v in cache.items() if v.get("t", "") >= limite.isoformat()}     # limpia lo viejo
    cerca = [e for e in proximos if e["estado"] == "pre" and
             dt.datetime.fromisoformat(e["fecha"].replace("Z", "+00:00")) - ahora <= dt.timedelta(hours=VENTANA_H)]
    cerca.sort(key=lambda e: (prioridad(e), e["fecha"]))
    equipos, vistos = [], set()
    for e in cerca:
        for lado in ("h", "a"):
            t = e[lado]
            if t["id"] in vistos:
                continue
            vistos.add(t["id"])
            c = cache.get(t["id"])
            if c and c.get("t", "") >= (ahora - dt.timedelta(hours=REFRESCO_H)).isoformat():
                continue
            equipos.append((t["id"], t["n"]))
    equipos = equipos[:MAX_BUSQUEDAS]

    def una(par):
        tid, nombre = par
        try:
            return tid, _buscar(nombre)
        except Exception:
            return tid, None

    nuevos = 0
    with ThreadPoolExecutor(4) as ex:
        for tid, items in ex.map(una, equipos):
            if items is not None:
                cache[tid] = {"t": ahora.isoformat(), "items": items}
                nuevos += 1
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ARCHIVO)
    return cache, nuevos, len(equipos)


def posibles_bajas(items, jugadores):
    """Jugadores (de la lista dada: [{id, n}]) nombrados en titulares de lesión/sanción/duda.
    Se compara el APELLIDO (última palabra del nombre, 4+ letras) con el titular, sin tildes."""
    out = {}
    for j in jugadores:
        partes = [p for p in _norm(j.get("n")).split() if len(p) >= 4]
        if not partes:
            continue
        ape = partes[-1]
        for it in items:
            if it["tipo"] in TIPOS_BAJA and re.search(r"\b" + re.escape(ape) + r"\b", _norm(it["titulo"])):
                out[j["id"]] = {"n": j.get("n"), "tipo": it["tipo"], "titulo": it["titulo"],
                                "fuente": it["fuente"], "fecha": it["fecha"], "link": it["link"]}
                break
    return out
