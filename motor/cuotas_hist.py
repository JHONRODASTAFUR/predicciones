"""Cuotas de cierre históricas de football-data.co.uk (gratis, sin cuenta; autorizado por el usuario).

Sirven SOLO para la prueba retrospectiva de las apuestas: medir con cuotas reales si la estrategia
de "apuesta de valor" habría ganado o perdido dinero (ROI) y calibrar cuánto pesa el modelo frente
al mercado. Se emparejan con los partidos de ESPN por liga, fecha (±1 día), marcador y nombres.
"""
import csv
import datetime as dt
import difflib
import io
import json
import re
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import DATOS, USER_AGENT

ARCHIVO = DATOS / "cuotas_historicas.json"
BASE_URL = "https://www.football-data.co.uk/"

PRINCIPALES = {"E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "SP2": "esp.2", "I1": "ita.1", "I2": "ita.2",
               "D1": "ger.1", "D2": "ger.2", "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "B1": "bel.1",
               "P1": "por.1", "T1": "tur.1", "G1": "gre.1", "SC0": "sco.1"}
EXTRA = {"ARG": "arg.1", "BRA": "bra.1", "MEX": "mex.1", "USA": "usa.1", "AUT": "aut.1", "DNK": "den.1",
         "NOR": "nor.1", "SWE": "swe.1", "JPN": "jpn.1", "CHN": "chn.1", "RUS": "rus.1"}

_RUIDO = {"fc", "cf", "ac", "sc", "cd", "ud", "rc", "afc", "club", "de", "sv", "fk", "sk", "if", "bk", "ca", "cr",
          "sd", "us", "as", "ss", "vfb", "vfl", "tsg", "1", "the", "and", "y"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in _RUIDO]
    return " ".join(toks)


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.95
    r = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(len(ta | tb), 1)
    return max(r, jac)


def _num(x):
    try:
        v = float(x)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _fecha(s):
    for f in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s.strip(), f).date()
        except (ValueError, AttributeError):
            continue
    return None


def _bajar(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def _filas(texto, extra):
    out = []
    for r in csv.DictReader(io.StringIO(texto)):
        f = _fecha(r.get("Date", ""))
        if not f:
            continue
        if extra:
            h, a, gh, ga = r.get("Home"), r.get("Away"), r.get("HG"), r.get("AG")
        else:
            h, a, gh, ga = r.get("HomeTeam"), r.get("AwayTeam"), r.get("FTHG"), r.get("FTAG")
        try:
            gh, ga = int(float(gh)), int(float(ga))
        except (TypeError, ValueError):
            continue
        # precio: Bet365 al cierre (una casa real); si falta o es incoherente, el promedio del mercado al cierre
        x12 = None
        for pref in ("B365C", "AvgC"):
            c1 = [_num(r.get(f"{pref}{k}")) for k in ("H", "D", "A")]
            if all(c1) and 1.0 <= sum(1 / x for x in c1) <= 1.20:     # margen posible; si no, dato corrupto
                x12 = c1
                break
        if not x12:
            continue
        ou25 = None
        for o_col, u_col in (("B365C>2.5", "B365C<2.5"), ("AvgC>2.5", "AvgC<2.5")):
            o, u = _num(r.get(o_col)), _num(r.get(u_col))   # los dos lados de la MISMA fuente
            if o and u and 1.0 <= 1 / o + 1 / u <= 1.15:
                ou25 = [o, u]
                break
        tarj = None
        try:
            tarj = sum(int(float(r.get(k))) for k in ("HY", "AY", "HR", "AR"))
        except (TypeError, ValueError):
            pass
        out.append({"fecha": f, "h": h, "a": a, "gh": gh, "ga": ga, "x12": x12, "ou25": ou25,
                    "ref": (r.get("Referee") or "").strip() or None, "tarj": tarj})
    return out


def arbitros_proximos(eventos):
    """Árbitro asignado a los próximos partidos (archivo fixtures.csv de football-data.co.uk; Inglaterra y
    Escocia). Devuelve {id_evento_espn: árbitro}."""
    try:
        texto = _bajar(BASE_URL + "fixtures.csv")
    except Exception:
        return {}
    out = {}
    pendientes = [e for e in eventos if e["estado"] == "pre" and e["lg"] in PRINCIPALES.values()]
    for r in csv.DictReader(io.StringIO(texto)):
        lg = PRINCIPALES.get((r.get("Div") or "").strip())
        ref = (r.get("Referee") or "").strip()
        f = _fecha(r.get("Date", ""))
        if not lg or not ref or not f:
            continue
        mejor, puntos = None, 0
        for e in pendientes:
            if e["lg"] != lg:
                continue
            d = dt.datetime.fromisoformat(e["fecha"].replace("Z", "+00:00")).date()
            if abs((d - f).days) > 1:
                continue
            s = _sim(r.get("HomeTeam"), e["h"]["n"]) + _sim(r.get("AwayTeam"), e["a"]["n"])
            if s > puntos:
                mejor, puntos = e, s
        if mejor and puntos >= 1.0:
            out[mejor["id"]] = ref
    return out


def sincronizar(eventos, forzar=False):
    """Descarga (una vez al día) y empareja. Devuelve {id_evento_espn: cuotas}."""
    previo = {}
    try:
        previo = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except Exception:
        pass
    hoy = dt.date.today().isoformat()
    if not forzar and previo.get("fecha") == hoy and "arbitros" in previo:
        return previo.get("cuotas", {}), previo.get("resumen", {})

    y = dt.date.today().year if dt.date.today().month >= 7 else dt.date.today().year - 1
    temporadas = [f"{(y - k) % 100:02d}{(y - k + 1) % 100:02d}" for k in (0, 1, 2)]
    urls = [(BASE_URL + f"mmz4281/{t}/{c}.csv", lg, False) for c, lg in PRINCIPALES.items() for t in temporadas]
    urls += [(BASE_URL + f"new/{c}.csv", lg, True) for c, lg in EXTRA.items()]

    def una(item):
        url, lg, extra = item
        try:
            return lg, _filas(_bajar(url), extra), None
        except Exception as e:
            return lg, [], f"{url}: {e}"

    por_liga, errores, ligas_con_error = {}, [], set()
    with ThreadPoolExecutor(6) as ex:
        for lg, filas, err in ex.map(una, urls):
            por_liga.setdefault(lg, []).extend(filas)
            if err:
                errores.append(err)
                ligas_con_error.add(lg)
    if len(errores) == len(urls):
        return previo.get("cuotas", {}), previo.get("resumen", {})
    ligas_por_evento = {e["id"]: e["lg"] for e in eventos}

    # índice de partidos jugados de ESPN por (liga, fecha, marcador)
    indice = {}
    for e in eventos:
        if not e.get("fin") or e["h"]["g"] is None or e["lg"] not in por_liga:
            continue
        d = dt.datetime.fromisoformat(e["fecha"].replace("Z", "+00:00")).date()
        for k in (-1, 0, 1):
            indice.setdefault((e["lg"], d + dt.timedelta(days=k), e["h"]["g"], e["a"]["g"]), []).append(e)

    cuotas, resumen = {}, {}
    arbitros = [[f["ref"], lg, f["tarj"], f["fecha"].isoformat()] for lg, filas in por_liga.items() for f in filas
                if f.get("ref") and f.get("tarj") is not None]
    for lg, filas in por_liga.items():
        n_ok = 0
        for f in filas:
            cands = indice.get((lg, f["fecha"], f["gh"], f["ga"]), [])
            if not cands:
                continue
            puntos = sorted(((_sim(f["h"], e["h"]["n"]) + _sim(f["a"], e["a"]["n"]), e) for e in cands),
                            key=lambda x: -x[0])
            mejor, e = puntos[0]
            segundo = puntos[1][0] if len(puntos) > 1 else 0
            if mejor >= 1.0 and (mejor - segundo >= 0.2 or len(puntos) == 1):
                cuotas[e["id"]] = {"x12": f["x12"], "ou25": f["ou25"]}
                n_ok += 1
        resumen[lg] = {"filas": len(filas), "emparejadas": n_ok}
    # si falló la descarga de alguna liga, se conservan las cuotas que ya se tenían de ella
    for eid, c in (previo.get("cuotas") or {}).items():
        if eid not in cuotas and ligas_por_evento.get(eid) in ligas_con_error:
            cuotas[eid] = c
    if not arbitros:
        arbitros = previo.get("arbitros", [])
    ARCHIVO.write_text(json.dumps({"fecha": hoy, "cuotas": cuotas, "resumen": resumen, "errores": errores[:20],
                                   "arbitros": arbitros}, ensure_ascii=False), encoding="utf-8")
    return cuotas, resumen


def arbitros_historial():
    """[(árbitro, liga, tarjetas del partido)] guardados en la última descarga."""
    try:
        return [tuple(x[:3]) for x in json.loads(ARCHIVO.read_text(encoding="utf-8")).get("arbitros", [])]
    except Exception:
        return []
