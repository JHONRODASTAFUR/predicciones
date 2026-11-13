"""Alineaciones, minutos y estadísticas por jugador y por equipo (resumen de partido de ESPN).

- Partidos jugados: se bajan una sola vez y se guardan compactos (para medir el aporte de cada jugador y el
  estilo de juego de cada equipo).
- Partidos por empezar: se consulta si ya hay alineación confirmada (ESPN la publica ~1 h antes).
"""
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from .config import DATOS
from .datos import http_json

URL_RESUMEN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/summary?event={id}"
CARPETA = DATOS / "resumenes"
PRINCIPALES = ["uefa.champions", "uefa.europa", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "por.1", "ned.1",
               "bra.1", "arg.1", "conmebol.libertadores", "conmebol.sudamericana", "per.1"]
# todas las competiciones (las de ESPN descubiertas incluidas): las principales primero
from .config import LIGAS as _LIGAS  # noqa: E402
LIGAS_JUGADORES = PRINCIPALES + [c for c, *_ in _LIGAS if c not in PRINCIPALES]
DESDE = "2025-01-01"            # historia de alineaciones de las principales
DESDE_RESTO = "2025-07-01"      # y del resto (menos historia para no hacer eterna la primera descarga)
ESTILO = {"possessionPct": "posesion", "totalShots": "remates", "shotsOnTarget": "remates_arco",
          "totalPasses": "pases", "accuratePasses": "pases_ok", "totalCrosses": "centros",
          "totalLongBalls": "largos", "effectiveTackles": "entradas", "interceptions": "intercepciones",
          "totalClearance": "despejes", "foulsCommitted": "faltas", "wonCorners": "corners"}
STATS_JUGADOR = {"totalGoals": "g", "goalAssists": "a", "totalShots": "sh", "shotsOnTarget": "sot", "saves": "sv",
                 "foulsCommitted": "fc", "foulsSuffered": "fs", "yellowCards": "yc", "redCards": "rc",
                 "goalsConceded": "gc"}


def linea(pos):
    """Línea del campo a partir de la abreviatura de ESPN (G, CD-L, LB, DM, AM, F, CF-R, ...)."""
    p = (pos or "").upper()
    if p.startswith("G"):
        return "POR"
    if any(p.startswith(x) for x in ("CD", "LB", "RB", "SW", "D", "LWB", "RWB")):
        return "DEF"
    if any(p.startswith(x) for x in ("F", "CF", "ST", "LW", "RW", "S")):
        return "DEL"
    return "MED"


def _minuto(clock):
    try:
        return min(int(str(clock.get("displayValue", "")).split("'")[0]), 120)
    except (ValueError, AttributeError):
        v = (clock or {}).get("value")
        return min(int(v // 60), 120) if v else None


def compactar(s, lg, eid):
    """Solo lo necesario: formación, jugadores con minutos y estadísticas, y estadísticas de estilo."""
    entradas, salidas = {}, {}
    for k in s.get("keyEvents") or []:
        if "ubstitution" not in ((k.get("type") or {}).get("text") or ""):
            continue
        m = _minuto(k.get("clock") or {})
        ps = k.get("participants") or []
        if len(ps) >= 2 and m is not None:
            entradas[str(ps[0]["athlete"]["id"])] = m
            salidas[str(ps[1]["athlete"]["id"])] = m
    estilo = {}
    for t in (s.get("boxscore") or {}).get("teams") or []:
        tid = str((t.get("team") or {}).get("id"))
        st = {x.get("name"): x.get("displayValue") for x in t.get("statistics") or []}
        estilo[tid] = {}
        for k, n in ESTILO.items():
            try:
                estilo[tid][n] = float(st.get(k))
            except (TypeError, ValueError):
                pass
    out = {"id": eid, "lg": lg, "equipos": {}}
    for r in s.get("rosters") or []:
        lado = "h" if r.get("homeAway") == "home" else "a"
        tid = str((r.get("team") or {}).get("id"))
        jugadores = []
        for p in r.get("roster") or []:
            at = p.get("athlete") or {}
            pid = str(at.get("id"))
            tit = bool(p.get("starter"))
            ent = 0 if tit else entradas.get(pid)
            if ent is None:
                continue                                   # suplente que no jugó
            sal = salidas.get(pid, 90)
            st = {x.get("name"): x.get("value") for x in p.get("stats") or []}
            j = {"id": pid, "n": at.get("displayName") or at.get("shortName"), "pos": (p.get("position") or {}).get(
                "abbreviation") or "", "tit": tit, "min": max(0, min(sal, 90) - min(ent, 90)),
                 "lugar": p.get("formationPlace")}
            for k, n in STATS_JUGADOR.items():
                if st.get(k) is not None:
                    j[n] = int(st[k])
            jugadores.append(j)
        out["equipos"][lado] = {"id": tid, "formacion": r.get("formation"), "jugadores": jugadores,
                                "estilo": estilo.get(tid, {})}
    return out if len(out["equipos"]) == 2 else None


ARCHIVO_TODO = DATOS / "resumenes.json"     # todos juntos: leer miles de archivos sueltos es muy lento


def _ruta(lg, eid):
    return CARPETA / lg / f"{eid}.json"


def _consolidar():
    """Pasa los archivos sueltos recién bajados al archivo único y los borra. Devuelve el diccionario completo."""
    try:
        todo = json.loads(ARCHIVO_TODO.read_text(encoding="utf-8"))
    except Exception:
        todo = {}
    sueltos = list(CARPETA.glob("*/*.json")) if CARPETA.exists() else []
    for f in sueltos:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            todo[d["id"]] = d
        except Exception:
            continue
    if sueltos:
        tmp = ARCHIVO_TODO.with_suffix(".tmp")
        tmp.write_text(json.dumps(todo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(ARCHIVO_TODO)
        for f in sueltos:
            try:
                f.unlink()
            except OSError:
                pass
    return todo


def sincronizar(eventos, maximo=None, avance=None):
    """Baja los resúmenes de partidos jugados que falten. Devuelve cuántos se bajaron."""
    ya = set(_consolidar())
    pendientes = [e for e in eventos if e["lg"] in LIGAS_JUGADORES and e["fin"] and e["id"] not in ya
                  and e["fecha"] >= (DESDE if e["lg"] in PRINCIPALES else DESDE_RESTO)]
    # primero las ligas principales y lo más reciente
    pendientes.sort(key=lambda e: (e["lg"] in PRINCIPALES, e["fecha"]), reverse=True)
    if maximo:
        pendientes = pendientes[:maximo]

    def una(e):
        try:
            s = http_json(URL_RESUMEN.format(lg=e["lg"], id=e["id"]), intentos=2)
            c = compactar(s, e["lg"], e["id"])
        except Exception:
            return 0
        ruta = _ruta(e["lg"], e["id"])
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(c or {"id": e["id"], "lg": e["lg"], "vacio": True}, ensure_ascii=False),
                        encoding="utf-8")
        return 1

    hechos = 0
    with ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(una, pendientes), 1):
            hechos += r
            if avance and i % 50 == 0:
                avance(i, len(pendientes))
    if hechos:
        _consolidar()
    return hechos, len(pendientes)


def cargar():
    """Todos los resúmenes guardados: {id_evento: resumen}."""
    return {k: v for k, v in _consolidar().items() if not v.get("vacio")}


def confirmadas(eventos, horas=3):
    """Alineaciones confirmadas de los partidos que empiezan en las próximas `horas` (o ya en juego)."""
    ahora = dt.datetime.now(dt.timezone.utc)
    cerca = [e for e in eventos if e["estado"] in ("pre", "in") and
             dt.datetime.fromisoformat(e["fecha"].replace("Z", "+00:00")) - ahora <= dt.timedelta(hours=horas)]

    def una(e):
        try:
            s = http_json(URL_RESUMEN.format(lg=e["lg"], id=e["id"]), intentos=2)
        except Exception:
            return e["id"], None
        out = {}
        for r in s.get("rosters") or []:
            tit = [p for p in r.get("roster") or [] if p.get("starter")]
            if len(tit) < 11:
                return e["id"], None
            lado = "h" if r.get("homeAway") == "home" else "a"
            out[lado] = {"formacion": r.get("formation"), "jugadores": [
                {"id": str(p["athlete"]["id"]), "n": p["athlete"].get("displayName"),
                 "pos": (p.get("position") or {}).get("abbreviation") or "", "lugar": p.get("formationPlace")}
                for p in tit]}
        return e["id"], out if len(out) == 2 else None

    res = {}
    with ThreadPoolExecutor(6) as ex:
        for eid, al in ex.map(una, cerca):
            if al:
                res[eid] = al
    return res
