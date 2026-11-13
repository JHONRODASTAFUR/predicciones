"""Descarga y caché de resultados, calendario, cuotas y tablas desde la API pública de ESPN."""
import datetime as dt
import gzip
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import CACHE, LIGAS, MESES_HISTORIA, USER_AGENT

URL_MARCADOR = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={ym}&limit=1000"
URL_TABLA = "https://site.api.espn.com/apis/v2/sports/soccer/{code}/standings"


CON_PRORROGA = ("STATUS_FINAL_AET", "STATUS_FINAL_PEN", "STATUS_FINAL_AGT")
VERSION_CACHE = 2       # 2: córners y goles del primer tiempo (los meses guardados antes se bajan de nuevo una vez)


class SinInternet(Exception):
    pass


def marcador_90(e):
    """Marcador a los 90' con el que liquidan las casas, o None si hubo prórroga y no se pudo reconstruir."""
    if e["h"]["g"] is None or e["a"]["g"] is None:
        return None
    if e.get("status") in CON_PRORROGA:
        g = e.get("g90")
        return (g[0], g[1]) if g else None
    return e["h"]["g"], e["a"]["g"]


def http_json(url, timeout=40, intentos=3, cabeceras=None):
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
                                                       **(cabeceras or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    b = gzip.decompress(b)
                return json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise
            ultimo = e
        except Exception as e:  # red caída, timeout, DNS
            ultimo = e
        time.sleep(1.5 * (i + 1))
    raise ultimo


def hay_internet():
    try:
        http_json(URL_MARCADOR.format(code="eng.1", ym=dt.date.today().strftime("%Y%m%d")), timeout=15, intentos=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- cuotas

def _americana_a_decimal(v):
    if v in (None, "", "OFF", "EVEN"):
        return 2.0 if v == "EVEN" else None
    try:
        x = float(str(v).replace("+", ""))
    except ValueError:
        return None
    if x == 0:
        return None
    return round(1 + (x / 100 if x > 0 else 100 / abs(x)), 4)


def _leer_cuotas(lista):
    if not lista:
        return None
    o = next((x for x in lista if x), None)
    if not o:
        return None
    ml = o.get("moneyline") or {}

    def lado(k, fuente):
        d = (ml.get(k) or {}).get(fuente) or {}
        return _americana_a_decimal(d.get("odds"))

    cierre = {"h": lado("home", "close"), "x": lado("draw", "close"), "a": lado("away", "close")}
    if cierre["x"] is None:
        cierre["x"] = _americana_a_decimal((o.get("drawOdds") or {}).get("moneyLine"))
    for k, kk in (("h", "homeTeamOdds"), ("a", "awayTeamOdds")):
        if cierre[k] is None:
            cierre[k] = _americana_a_decimal((o.get(kk) or {}).get("moneyLine"))
    apertura = {"h": lado("home", "open"), "x": lado("draw", "open"), "a": lado("away", "open")}
    if not all(cierre.values()):
        return None
    res = {"cierre": cierre, "proveedor": (o.get("provider") or {}).get("name", "")}
    if all(apertura.values()):
        res["apertura"] = apertura
    if o.get("overUnder") is not None:
        res["linea_goles"] = o.get("overUnder")
    tot = o.get("total") or {}
    try:
        linea = float(str(((tot.get("over") or {}).get("close") or {}).get("line", "")).lstrip("ou"))
    except ValueError:
        linea = None
    over = _americana_a_decimal(((tot.get("over") or {}).get("close") or {}).get("odds"))
    under = _americana_a_decimal(((tot.get("under") or {}).get("close") or {}).get("odds"))
    if linea is not None and over and under and abs(linea * 2 - round(linea * 2)) < 1e-9 and linea % 1 == 0.5:
        res["goles"] = {"linea": linea, "over": over, "under": under}
    return res


# ---------------------------------------------------------------- eventos

def compactar(e, code):
    c = (e.get("competitions") or [{}])[0]
    st = (e.get("status") or {}).get("type") or {}
    lados = {x.get("homeAway"): x for x in c.get("competitors") or []}
    h, a = lados.get("home"), lados.get("away")
    if not h or not a:
        return None
    fin = bool(st.get("completed"))
    con_marcador = fin or st.get("state") == "in"

    def equipo(x):
        t = x.get("team") or {}
        g = None
        if con_marcador:
            try:
                g = int(x.get("score"))
            except (TypeError, ValueError):
                g = None
        return {"id": str(t.get("id")), "n": t.get("displayName") or t.get("name"),
                "c": t.get("abbreviation") or "", "logo": t.get("logo") or "", "g": g}

    eh, ea = equipo(h), equipo(a)
    tarj = {"h": [0, 0], "a": [0, 0]}
    pen = {"h": 0, "a": 0}
    g90 = {"h": 0, "a": 0}
    g1t = {"h": 0, "a": 0}
    detalles = c.get("details") or []
    for d in detalles:
        tid = str((d.get("team") or {}).get("id"))
        lado = "h" if tid == eh["id"] else "a" if tid == ea["id"] else None
        if not lado:
            continue
        if d.get("scoringPlay") and not d.get("shootout"):
            minuto = re.match(r"(\d+)", str((d.get("clock") or {}).get("displayValue") or ""))
            if minuto and int(minuto.group(1)) <= 90:      # "90'+3'" cuenta; "105'" no
                g90[lado] += 1
            if minuto and int(minuto.group(1)) <= 45:      # primer tiempo, con el añadido ("45'+2'")
                g1t[lado] += 1
        if d.get("yellowCard"):
            tarj[lado][0] += 1
        if d.get("redCard"):
            tarj[lado][1] += 1
        if d.get("penaltyKick") and not d.get("shootout"):
            pen[lado] += 1
    ven = c.get("venue") or {}
    temp = e.get("season") or {}
    # Las casas liquidan con el marcador a los 90' (sin prórroga ni penales): se reconstruye con los goles detallados
    marcador_90 = None
    if fin and st.get("name") in CON_PRORROGA and detalles:
        marcador_90 = [g90["h"], g90["a"]]
    # córners y primer tiempo: solo si el dato existe (ligas chicas traen todo en 0)
    corners = None
    stats = [{s.get("name"): s.get("displayValue") for s in (x.get("statistics") or [])} for x in (h, a)]
    try:
        if all(s.get("wonCorners") is not None for s in stats) and any(
                float(s.get(k) or 0) > 0 for s in stats for k in ("possessionPct", "totalShots", "wonCorners")):
            corners = [int(float(stats[0]["wonCorners"])), int(float(stats[1]["wonCorners"]))]
    except ValueError:
        corners = None
    primer_tiempo = None
    if fin and detalles and eh["g"] is not None:
        # solo si los goles detallados cuadran con el marcador (si no, falta información)
        if g90["h"] == (marcador_90 or [eh["g"], ea["g"]])[0] and g90["a"] == (marcador_90 or [eh["g"], ea["g"]])[1]:
            primer_tiempo = [g1t["h"], g1t["a"]]
    elif fin and eh["g"] == 0 and ea["g"] == 0:
        primer_tiempo = [0, 0]
    return {
        "id": str(e.get("id")), "lg": code, "fecha": e.get("date"),
        "temporada": f"{temp.get('year', '')}-{temp.get('slug', '')}",
        "estado": st.get("state"), "fin": fin, "status": st.get("name"),
        "detalle": st.get("shortDetail") or st.get("detail") or "",
        "h": eh, "a": ea, "neutral": bool(c.get("neutralSite")),
        "estadio": ven.get("fullName") or "", "ciudad": (ven.get("address") or {}).get("city") or "",
        "pais": (ven.get("address") or {}).get("country") or "",
        "tarj": tarj, "pen": pen, "con_detalle": bool(detalles), "g90": marcador_90,
        "cor": corners, "g1t": primer_tiempo,
        "notas": [n.get("headline") for n in c.get("notes") or [] if n.get("headline")],
        "cuotas": _leer_cuotas(c.get("odds")),
    }


def _meses(desde, hasta):
    y, m = desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _fin_de_mes(y, m):
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))


def sincronizar(avance=None):
    """Descarga lo que falte y devuelve la lista de partidos (compactados) de todas las ligas."""
    hoy = dt.date.today()
    inicio = dt.date(hoy.year, hoy.month, 1)
    for _ in range(MESES_HISTORIA):
        inicio = (inicio - dt.timedelta(days=1)).replace(day=1)
    hasta = (hoy.replace(day=1) + dt.timedelta(days=40))
    desde_cerca = (hoy - dt.timedelta(days=3)).isoformat()
    hasta_cerca = (hoy + dt.timedelta(days=11)).isoformat()
    tareas = []
    for code, *_ in LIGAS:
        for y, m in _meses(inicio, hasta):
            ruta = CACHE / code / f"{y}{m:02d}.json"
            cerrado = _fin_de_mes(y, m) + dt.timedelta(days=12) < hoy
            if ruta.exists():
                try:
                    previo = json.loads(ruta.read_text(encoding="utf-8"))
                    if previo.get("v", 1) >= VERSION_CACHE:
                        if cerrado and previo.get("cerrado"):
                            continue
                        # mes abierto de una liga sin partidos cerca (receso): basta con refrescarlo una vez al día
                        hace = dt.datetime.now() - dt.datetime.fromisoformat(previo.get("bajado", "2000-01-01"))
                        cerca = any(desde_cerca <= e["fecha"][:10] <= hasta_cerca for e in previo.get("eventos", []))
                        if not cerrado and not cerca and hace < dt.timedelta(hours=20):
                            continue
                except Exception:
                    pass
            tareas.append((code, y, m, ruta, cerrado))

    errores = []

    def bajar(t):
        code, y, m, ruta, cerrado = t
        try:
            d = http_json(URL_MARCADOR.format(code=code, ym=f"{y}{m:02d}"))
            evs = [x for x in (compactar(e, code) for e in d.get("events") or []) if x]
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                evs = []          # liga sin partidos ese mes
            else:
                errores.append(f"{code} {y}-{m:02d}: {e}")
                return
        except Exception as e:
            errores.append(f"{code} {y}-{m:02d}: {e}")
            return
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cerrado": cerrado, "bajado": dt.datetime.now().isoformat(timespec="seconds"),
                                   "v": VERSION_CACHE, "eventos": evs}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ruta)

    hechos = 0
    with ThreadPoolExecutor(8) as ex:
        for _ in ex.map(bajar, tareas):
            hechos += 1
            if avance and (hechos % 10 == 0 or hechos == len(tareas)):
                avance(hechos, len(tareas))
    if tareas and len(errores) == len(tareas):
        raise SinInternet("No se pudo conectar con ESPN")
    return cargar_cache(), errores, len(tareas)


def cargar_cache():
    vistos = {}
    for code, *_ in LIGAS:
        carpeta = CACHE / code
        if not carpeta.exists():
            continue
        for f in sorted(carpeta.glob("*.json")):
            try:
                for e in json.loads(f.read_text(encoding="utf-8")).get("eventos", []):
                    vistos[e["id"]] = e
            except Exception:
                continue
    return list(vistos.values())


_EXCLUIR = ("women", "wwc", ".w.", "u17", "u19", "u20", "u21", "u23", "youth", "friendly", "nwsl", "femenina",
            "reina", "shebelieves", "pinatar", "arnold", "ncaa", "joan_gamper", "club_challenge", "world_challenge",
            "charity", "olympic")
_PAISES_ES = {"eng": "Inglaterra", "esp": "España", "ger": "Alemania", "ita": "Italia", "fra": "Francia",
              "ned": "Países Bajos", "por": "Portugal", "sco": "Escocia", "bel": "Bélgica", "tur": "Turquía",
              "gre": "Grecia", "aut": "Austria", "den": "Dinamarca", "nor": "Noruega", "swe": "Suecia", "rus": "Rusia",
              "usa": "EE. UU.", "mex": "México", "arg": "Argentina", "bra": "Brasil", "chi": "Chile", "col": "Colombia",
              "uru": "Uruguay", "per": "Perú", "par": "Paraguay", "ecu": "Ecuador", "ven": "Venezuela", "bol": "Bolivia",
              "jpn": "Japón", "chn": "China", "ksa": "Arabia Saudita", "aus": "Australia", "ind": "India",
              "rsa": "Sudáfrica", "hon": "Honduras", "crc": "Costa Rica", "gua": "Guatemala", "slv": "El Salvador",
              "uefa": "Europa", "conmebol": "Sudamérica", "concacaf": "Norteamérica", "caf": "África", "afc": "Asia",
              "fifa": "Mundial", "global": "Internacional", "aff": "Sudeste asiático", "campeones": "Norteamérica"}


def descubrir_ligas():
    """Todas las competiciones masculinas de ESPN (se guarda en datos/ligas_espn.json, una vez por semana)."""
    from .config import ARCHIVO_LIGAS_ESPN
    try:
        previo = json.loads(ARCHIVO_LIGAS_ESPN.read_text(encoding="utf-8"))
        if (dt.date.today() - dt.date.fromisoformat(previo["fecha"])).days < 7:
            return previo["ligas"]
    except Exception:
        pass
    d = http_json("https://site.api.espn.com/apis/site/v2/leagues/dropdown?sport=soccer&limit=500")
    ligas = []
    for l in d.get("leagues") or []:
        slug, nombre = l.get("slug") or "", l.get("name") or ""
        if not slug or any(x in (slug + " " + nombre).lower() for x in _EXCLUIR):
            continue
        tipo = "liga" if re.fullmatch(r"[a-z]{3}\.\d", slug) else "copa"
        ligas.append({"codigo": slug, "nombre": nombre, "pais": _PAISES_ES.get(slug.split(".")[0], ""), "tipo": tipo})
    from .config import ARCHIVO_LIGAS_ESPN as ruta
    ruta.write_text(json.dumps({"fecha": dt.date.today().isoformat(), "ligas": ligas}, ensure_ascii=False),
                    encoding="utf-8")
    return ligas


def bajar_tablas(codigos):
    """Tablas de posiciones oficiales de ESPN para las ligas indicadas."""
    tablas = {}

    def una(code):
        try:
            d = http_json(URL_TABLA.format(code=code), intentos=2)
        except Exception:
            return code, None
        grupos = []
        hijos = d.get("children") or ([d] if d.get("standings") else [])
        for ch in hijos:
            st = ch.get("standings") or {}
            filas = []
            for en in st.get("entries") or []:
                s = {x.get("name"): x.get("value") for x in en.get("stats") or []}
                t = en.get("team") or {}
                nota = en.get("note") or {}
                filas.append({
                    "id": str(t.get("id")), "n": t.get("displayName"), "logo": (t.get("logos") or [{}])[0].get("href", ""),
                    "pos": int(s.get("rank") or 0), "pj": int(s.get("gamesPlayed") or 0),
                    "g": int(s.get("wins") or 0), "e": int(s.get("ties") or 0), "p": int(s.get("losses") or 0),
                    "gf": int(s.get("pointsFor") or 0), "gc": int(s.get("pointsAgainst") or 0),
                    "dg": int(s.get("pointDifferential") or 0), "pts": int(s.get("points") or 0),
                    "nota": nota.get("description") or "", "color": nota.get("color") or "",
                })
            filas.sort(key=lambda r: (r["pos"] or 999, -r["pts"]))
            if filas:
                grupos.append({"nombre": ch.get("name") or st.get("seasonDisplayName") or "", "filas": filas})
        return code, grupos or None

    with ThreadPoolExecutor(6) as ex:
        for code, g in ex.map(una, codigos):
            if g:
                tablas[code] = g
    return tablas
