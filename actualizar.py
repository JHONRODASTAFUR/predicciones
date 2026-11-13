"""Actualiza datos y predicciones. Lo ejecuta solo la tarea programada de Windows (y el botón de la app).

Uso:  pythonw actualizar.py          (sin ventana)
      python  actualizar.py --forzar (recalcula también la validación)
"""
import datetime as dt
import json
import logging
import logging.handlers
import math
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from motor import alineaciones, apuestas, cuotas_hist, jugadores  # noqa: E402
from motor import mercados_extra as mx  # noqa: E402
from motor import config as C  # noqa: E402
from motor import datos, geo, modelo  # noqa: E402
from motor import conjunto  # noqa: E402


def con_conjunto(D, P, jugados):
    """Ligas de nivel 3-4: el modelo principal se combina con uno de memoria corta y con el Elo (motor/conjunto.py)."""
    try:
        Pc = modelo.ajustar(D, jugados, D.ahora, conjunto.VIDA_CORTA, iters=200)
        P.conj = conjunto.Conjunto(D, Pc, D.ahora)
    except Exception:
        log.warning("Sin conjunto (memoria corta + Elo); se usa solo el modelo principal: %s",
                    traceback.format_exc(limit=3))
        P.conj = None
    return P

log = logging.getLogger("actualizar")
ARCHIVO_TABLAS = C.DATOS / "tablas.json"
ARCHIVO_LAMBDAS_OOS = C.DATOS / "lambdas_fuera_de_muestra.json"
VENTANA_RAPIDA_H = 2.5          # la revisión de cada 15 min mira los partidos que empiezan en este plazo


# ------------------------------------------------------------------ utilidades

def ahora_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def leer_json(ruta, defecto):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return defecto


def escribir_json(ruta, obj):
    ruta = Path(ruta)
    tmp = ruta.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())          # que un corte de luz no deje el archivo a medias
    for _ in range(10):
        try:
            tmp.replace(ruta)
            return
        except PermissionError:      # la app lo está leyendo justo ahora
            time.sleep(0.3)
    tmp.replace(ruta)


def leer_historial():
    """El historial es lo único que no se puede volver a descargar: nunca se pisa si no se pudo leer."""
    ruta = C.ARCHIVO_HISTORIAL
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        dañado = ruta.with_name(f"historial.corrupto-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(ruta, dañado)
        log.error("historial.json no se pudo leer; copia guardada en %s; se usa el respaldo", dañado.name)
        respaldo = ruta.with_suffix(".bak")
        try:
            return json.loads(respaldo.read_text(encoding="utf-8"))
        except Exception:
            raise RuntimeError("El historial y su respaldo están dañados; revisa la carpeta datos")


def guardar_historial(hist):
    ruta = C.ARCHIVO_HISTORIAL
    if ruta.exists():
        try:
            json.loads(ruta.read_text(encoding="utf-8"))     # solo se respalda una versión que se puede leer
            shutil.copy2(ruta, ruta.with_suffix(".bak"))
        except Exception:
            pass
    escribir_json(ruta, hist)


def estado(**kw):
    e = leer_json(C.ARCHIVO_ESTADO, {})
    e.update(kw)
    e["actualizado"] = ahora_iso()
    escribir_json(C.ARCHIVO_ESTADO, e)


def configurar_log():
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(C.LOGS / "actualizar.log", maxBytes=1_000_000, backupCount=3,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    if sys.stdout and sys.stdout.isatty():
        log.addHandler(logging.StreamHandler(sys.stdout))


def tomar_candado():
    """Evita dos actualizaciones a la vez (tarea programada + botón)."""
    f = open(C.ARCHIVO_LOCK, "a+")
    try:
        if os.name == "nt":                    # Windows (laptop)
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:                                  # Linux (servidor 24/7)
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# ------------------------------------------------------------------ mercado (D24.15)

def prob_mercado(cuotas):
    if not cuotas:
        return None
    ci = cuotas["cierre"]
    inv = {k: 1 / v for k, v in ci.items() if v}
    s = sum(inv.values())
    if len(inv) != 3 or s <= 0:
        return None
    out = {"pH": round(inv["h"] / s, 4), "pD": round(inv["x"] / s, 4), "pA": round(inv["a"] / s, 4),
           "margen": round(s - 1, 4), "proveedor": cuotas.get("proveedor", ""),
           "cuotas": {k: round(v, 2) for k, v in ci.items()}}
    ap = cuotas.get("apertura")
    if ap:
        inv2 = {k: 1 / v for k, v in ap.items()}
        s2 = sum(inv2.values())
        out["apertura"] = {"pH": round(inv2["h"] / s2, 4), "pD": round(inv2["x"] / s2, 4),
                           "pA": round(inv2["a"] / s2, 4)}
    if cuotas.get("linea_goles") is not None:
        out["linea_goles"] = cuotas["linea_goles"]
    return out


# ------------------------------------------------------------------ contexto de cada partido

def resultado_para(e, equipo_id):
    soy_local = e["h"]["id"] == equipo_id
    gf, gc = (e["h"]["g"], e["a"]["g"]) if soy_local else (e["a"]["g"], e["h"]["g"])
    r = "G" if gf > gc else "E" if gf == gc else "P"
    rival = e["a"] if soy_local else e["h"]
    return {"r": r, "gf": gf, "gc": gc, "rival": rival["n"], "loc": soy_local, "lg": e["lg"],
            "fecha": e["fecha"], "id": e["id"]}


def contexto(D, e, por_equipo, tablas):
    t = e["fecha"]
    out = {}
    for lado in ("h", "a"):
        tid = e[lado]["id"]
        lista = por_equipo.get(tid, [])
        previos = [x for x in lista if x["fecha"] < t and x["fin"] and x["h"]["g"] is not None][-6:]
        siguientes = [x for x in lista if x["fecha"] > t][:3]
        ult = [resultado_para(x, tid) for x in previos][::-1][:5]
        i = D.idx[tid]
        desc = D.descanso.get((e["id"], i))
        rot = D.rotacion.get((e["id"], i))
        pos = None
        for g in tablas.get(e["lg"], []):
            for f in g["filas"]:
                if f["id"] == tid:
                    pos = {"pos": f["pos"], "pts": f["pts"], "pj": f["pj"], "dg": f["dg"], "grupo": g["nombre"],
                           "nota": f["nota"], "total": len(g["filas"])}
        out[lado] = {
            "ultimos": ult, "forma": "".join(x["r"] for x in ult),
            "proximos": [{"fecha": x["fecha"], "lg": x["lg"], "rival": (x["a"] if x["h"]["id"] == tid else x["h"])["n"],
                          "loc": x["h"]["id"] == tid} for x in siguientes],
            "descanso": round(desc, 1) if desc is not None else None,
            "rotacion": C.INFO_LIGA.get(rot, {}).get("nombre") if rot else None,
            "tabla": pos,
        }
    # historial directo (D15.6)
    hid, aid = e["h"]["id"], e["a"]["id"]
    h2h = [x for x in por_equipo.get(hid, []) if x["fin"] and x["fecha"] < t and
           {x["h"]["id"], x["a"]["id"]} == {hid, aid} and x["h"]["g"] is not None][-6:][::-1]
    out["h2h"] = [{"fecha": x["fecha"], "lg": x["lg"], "local": x["h"]["n"], "visita": x["a"]["n"],
                   "gl": x["h"]["g"], "gv": x["a"]["g"]} for x in h2h]
    # partido de ida en eliminatorias
    ida = None
    if C.INFO_LIGA.get(e["lg"], {}).get("tipo") == "copa":
        for x in h2h:
            if x["lg"] == e["lg"]:
                dias = (dt.datetime.fromisoformat(t.replace("Z", "+00:00")) -
                        dt.datetime.fromisoformat(x["fecha"].replace("Z", "+00:00"))).days
                if dias <= 60:
                    ida = x
                break
    out["ida"] = {"local": ida["h"]["n"], "visita": ida["a"]["n"], "gl": ida["h"]["g"], "gv": ida["a"]["g"]} \
        if ida else None
    return out


def señales(e, pred, ctx, clima, mercado, alt_estadio, D):
    s = []
    X = pred["calc"]
    if X["dalt_km"] >= 0.8 and alt_estadio:
        s.append({"tipo": "altitud", "nivel": "rojo" if X["dalt_km"] >= 2 else "amarillo",
                  "texto": f"Estadio a {alt_estadio:.0f} m: el visitante sube {X['dalt_km'] * 1000:.0f} m (D18.1)"})
    for lado, nombre in (("h", e["h"]["n"]), ("a", e["a"]["n"])):
        d = ctx[lado]["descanso"]
        otro = ctx["a" if lado == "h" else "h"]["descanso"]
        # 3 días entre partidos es lo normal en semanas de copa: solo se avisa si es muy corto
        # o si un equipo descansó claramente menos que el otro
        if d is not None and (d <= 2.5 or (d <= 3.5 and otro is not None and otro - d >= 2)):
            s.append({"tipo": "descanso", "nivel": "amarillo",
                      "texto": f"{nombre}: solo {d:.1f} días de descanso (D24.8)"})
        if ctx[lado]["rotacion"]:
            s.append({"tipo": "rotacion", "nivel": "amarillo",
                      "texto": f"{nombre} juega {ctx[lado]['rotacion']} en 4 días o menos: posible rotación (D24.6)"})
    if clima:
        t, ll, pl, vi = clima.get("temp"), clima.get("lluvia_mm"), clima.get("prob_lluvia"), clima.get("viento")
        if t is not None and t >= 30:
            s.append({"tipo": "clima", "nivel": "rojo" if t >= 34 else "amarillo",
                      "texto": f"Calor: {t:.0f} °C a la hora del partido (D18.2)"})
        if t is not None and t <= 2:
            s.append({"tipo": "clima", "nivel": "amarillo", "texto": f"Frío: {t:.0f} °C (D18.2)"})
        if (ll is not None and ll >= 1) or (pl is not None and pl >= 70):
            s.append({"tipo": "clima", "nivel": "amarillo",
                      "texto": f"Lluvia probable ({pl or 0:.0f}%, {ll or 0:.1f} mm/h): césped rápido o pesado (D18.3)"})
        if vi is not None and vi >= 35:
            s.append({"tipo": "clima", "nivel": "amarillo", "texto": f"Viento fuerte: {vi:.0f} km/h (D18.3)"})
    if pred["conf"] == "baja":
        s.append({"tipo": "datos", "nivel": "amarillo",
                  "texto": "Pocos partidos recientes de uno de los equipos: intervalo amplio (D24.11)"})
    if mercado:
        dif = max(abs(pred["pH"] - mercado["pH"]), abs(pred["pD"] - mercado["pD"]), abs(pred["pA"] - mercado["pA"]))
        if dif >= 0.10:
            s.append({"tipo": "mercado", "nivel": "amarillo",
                      "texto": f"Modelo y mercado difieren {dif * 100:.0f} puntos: el mercado puede saber de bajas o "
                               f"alineaciones (D24.15)"})
        ap = mercado.get("apertura")
        if ap:
            mov = max(abs(mercado["pH"] - ap["pH"]), abs(mercado["pA"] - ap["pA"]))
            if mov >= 0.05:
                s.append({"tipo": "mercado", "nivel": "amarillo",
                          "texto": f"Las cuotas se movieron {mov * 100:.0f} puntos desde la apertura: posible "
                                   f"información nueva (D24.15)"})
    if ctx.get("ida"):
        i = ctx["ida"]
        s.append({"tipo": "eliminatoria", "nivel": "info",
                  "texto": f"Ida: {i['local']} {i['gl']}-{i['gv']} {i['visita']} (D24.7)"})
    return s


# ------------------------------------------------------------------ historial (D24.17)

def metricas_partido(p, yh, ya):
    oH, oD, oA = float(yh > ya), float(yh == ya), float(yh < ya)
    rps = 0.5 * ((p["pH"] - oH) ** 2 + (p["pH"] + p["pD"] - oH - oD) ** 2)
    brier = (p["pH"] - oH) ** 2 + (p["pD"] - oD) ** 2 + (p["pA"] - oA) ** 2
    pr = max(p["pH"] * oH + p["pD"] * oD + p["pA"] * oA, 1e-6)
    fav = max(("H", p["pH"]), ("D", p["pD"]), ("A", p["pA"]), key=lambda x: x[1])[0]
    real = "H" if oH else "D" if oD else "A"
    return {"rps": round(rps, 4), "brier": round(brier, 4), "logloss": round(-math.log(pr), 4),
            "acierto": fav == real, "real": real,
            "o25_ok": (p.get("o25", 0.5) >= 0.5) == (yh + ya > 2.5),
            "btts_ok": (p.get("btts", 0.5) >= 0.5) == (yh > 0 and ya > 0)}


def actualizar_historial(hist, partidos_pred, eventos_por_id):
    ahora = dt.datetime.now(dt.timezone.utc)
    for item in partidos_pred:
        # lo registrado "antes del partido" no se toca una vez llegada la hora de inicio, aunque ESPN aún diga "pre"
        if item["estado"] != "pre" or dt.datetime.fromisoformat(item["fecha"].replace("Z", "+00:00")) <= ahora:
            continue
        p = item["pred"]
        reg = hist.get(item["id"])
        nuevo = {"pH": p["pH"], "pD": p["pD"], "pA": p["pA"], "lh": p["lh"], "la": p["la"],
                 "o25": p["o25"], "btts": p["btts"], "top": p["top"][0], "ic": p["ic"],
                 "guardado": ahora.isoformat(timespec="seconds")}
        if reg is None:
            reg = hist[item["id"]] = {"lg": item["lg"], "fecha": item["fecha"], "h": item["h"]["n"],
                                      "a": item["a"]["n"], "primera": nuevo}
        reg["fecha"] = item["fecha"]
        reg["ultima"] = nuevo
        if item.get("mercado"):
            reg["mercado"] = {k: item["mercado"][k] for k in ("pH", "pD", "pA")}
        ap = item.get("apuesta")
        # si ESPN retira las cuotas justo antes del inicio, se conserva la recomendación basada en cuotas
        if ap and not (ap["base"] == "modelo" and (reg.get("apuesta") or {}).get("base") == "mercado"):
            por_cod = {f["cod"]: f for f in ap["mercados"]}
            reg["apuesta"] = {"base": ap["base"], "perfiles": {}}
            for perfil, x in ap["perfiles"].items():
                f = por_cod[x["cod"]]
                reg["apuesta"]["perfiles"][perfil] = {
                    "cod": f["cod"], "txt": f["txt"], "p": f["p"], "p10": f["p10"], "p90": f["p90"],
                    "prud": f["prud"], "p_dev": f["p_dev"], "justa": f["justa"], "minima": f["minima"],
                    "riesgo": x["riesgo"], "cuota_casa": f.get("cuota_casa")}
            if ap.get("valor"):
                v = ap["valor"]
                reg["apuesta"]["valor"] = {**v, "cuota_casa": por_cod[v["cod"]].get("cuota_casa")}
    for eid, reg in hist.items():
        if reg.get("res"):
            continue
        e = eventos_por_id.get(eid)
        if e and e["fin"] and e["h"]["g"] is not None and "ultima" in reg:
            m90 = datos.marcador_90(e)      # las casas y el modelo usan el marcador a los 90'
            if m90 is None:                 # prórroga sin goles detallados: no se puede liquidar con certeza
                reg["sin_liquidar"] = True
                continue
            yh, ya = m90
            reg["res"] = [yh, ya]
            if (yh, ya) != (e["h"]["g"], e["a"]["g"]):
                reg["res_final"] = [e["h"]["g"], e["a"]["g"]]
            reg["met"] = metricas_partido(reg["ultima"], yh, ya)
            if reg.get("mercado"):
                reg["met_mercado"] = metricas_partido(reg["mercado"], yh, ya)
            for x in (reg.get("apuesta") or {}).get("perfiles", {}).values():
                x["res"] = modelo.liquidar(x["cod"], yh, ya)
                if x.get("cuota_casa"):
                    x["ganancia"] = round(x["cuota_casa"] - 1 if x["res"] == "gana" else
                                          (0.0 if x["res"] == "devuelve" else -1.0), 3)
    limite = (ahora - dt.timedelta(days=400)).isoformat()
    for eid in [k for k, v in hist.items() if v.get("fecha", "") < limite]:
        del hist[eid]
    return hist


def resumen_historial(hist):
    cerrados = [v for v in hist.values() if v.get("met")]
    if not cerrados:
        return {"n": 0}

    def bloque(lst):
        if not lst:
            return None
        n = len(lst)
        b = {"n": n,
             "acierto": round(sum(v["met"]["acierto"] for v in lst) / n, 4),
             "rps": round(sum(v["met"]["rps"] for v in lst) / n, 4),
             "brier": round(sum(v["met"]["brier"] for v in lst) / n, 4),
             "logloss": round(sum(v["met"]["logloss"] for v in lst) / n, 4),
             "o25": round(sum(v["met"]["o25_ok"] for v in lst) / n, 4),
             "btts": round(sum(v["met"]["btts_ok"] for v in lst) / n, 4)}
        conm = [v for v in lst if v.get("met_mercado")]
        if conm:
            b["n_mercado"] = len(conm)
            b["rps_modelo_vs"] = round(sum(v["met"]["rps"] for v in conm) / len(conm), 4)
            b["rps_mercado"] = round(sum(v["met_mercado"]["rps"] for v in conm) / len(conm), 4)
        return b

    hace30 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    por_liga = {}
    for v in cerrados:
        por_liga.setdefault(v["lg"], []).append(v)
    # recomendaciones de apuesta registradas antes de cada partido
    ap = {}
    for perfil in apuestas.PERFILES:
        xs = [v["apuesta"]["perfiles"][perfil] for v in cerrados
              if (v.get("apuesta") or {}).get("perfiles", {}).get(perfil, {}).get("res")]
        if not xs:
            continue
        dec = [x for x in xs if x["res"] != "devuelve"]
        con_cuota = [x["ganancia"] for x in xs if "ganancia" in x]
        ap[perfil] = {"n": len(xs), "acierto": round(sum(x["res"] == "gana" for x in dec) / max(len(dec), 1), 4),
                      "esperado": round(sum(x["p"] for x in xs) / len(xs), 4),
                      "n_cuota": len(con_cuota),
                      "roi": round(sum(con_cuota) / len(con_cuota), 4) if con_cuota else None,
                      "unidades": round(sum(con_cuota), 2) if con_cuota else None}
    return {"n": len(cerrados), "total": bloque(cerrados),
            "ult30": bloque([v for v in cerrados if v["fecha"] >= hace30]),
            "por_liga": {k: bloque(v) for k, v in por_liga.items()}, "apuestas": ap}


# ------------------------------------------------------------------ córners, tarjetas y tiempos

def preparar_extras(D, vida, ajuste):
    """Modelos de córners, tarjetas y primer tiempo, árbitros y calibración guardada."""
    sel_tarj = {lg for lg, cov in D.cobertura.items() if cov >= 0.7}
    props, glob = mx.proporcion_1t(D.eventos, D.ahora)
    arb = mx.efecto_arbitro(cuotas_hist.arbitros_historial())
    try:
        asignados = cuotas_hist.arbitros_proximos(D.eventos)
    except Exception:
        asignados = {}
    return {"cor": mx.ModeloConteo(D.eventos, "cor", D.idx, D.ahora, vida),
            "tarj": mx.ModeloConteo(D.eventos, "tarj", D.idx, D.ahora, vida, sel_comp=sel_tarj),
            "props": props, "glob": glob, "arb": arb, "asignados": asignados,
            "cal": ajuste.get("extras") or {}, "factor": ajuste.get("extras_factor") or {}}


def _con_cuotas(filas, cal):
    """Cuota justa, mínima y de alerta; sin mínima si el mercado no pasó la prueba de calibración."""
    for f in filas:
        f["p"] = round(min(max(f["p"], 1e-4), 1 - 1e-4), 4)
        f["justa"] = round(1 / f["p"], 2)
        c = cal.get(f["cod"])
        if c and c.get("ok"):
            f["minima"] = apuestas.cuota_minima(f["p"], f["p"], "modelo")
            f["alerta"] = apuestas.cuota_alerta(f["p"], f["minima"])
        else:
            f["no_calibrado"] = True
        if c:
            f["prueba"] = {k: c[k] for k in ("n", "esperado", "real")}
    return filas


def construir_extras(e, pred, D, ex):
    out = {}
    L, V = e["h"]["n"], e["a"]["n"]
    if ex["cor"].ok and e["lg"] in ex["cor"].cidx:
        mh, ma, phi = ex["cor"].medias(e, D.idx)
        f = ex["factor"].get("cor", 1.0)
        mh, ma = mh * f, ma * f
        filas, esp = mx.mercados_conteo(mh, ma, phi, mx.LINEAS_CORNERS, mx.LINEAS_CORNERS_EQ, "cor", L, V)
        out["corners"] = {"local": round(mh, 2), "visita": round(ma, 2), "total": round(esp, 2), "phi": round(phi, 3),
                          "media_liga": round(ex["cor"].media_comp.get(e["lg"], 0), 2),
                          "mercados": _con_cuotas(filas, ex["cal"].get("cor", {}))}
    if ex["tarj"].ok and e["lg"] in ex["tarj"].cidx:
        mh, ma, phi = ex["tarj"].medias(e, D.idx)
        f = ex["factor"].get("tarj", 1.0)
        mh, ma = mh * f, ma * f
        ref = ex["asignados"].get(e["id"])
        info_ref = None
        if ref:
            a = ex["arb"].get(ref)
            factor = a["factor"] if a else 1.0
            mh, ma = mh * factor, ma * factor
            info_ref = {"nombre": ref, **(a or {"n": 0, "factor": 1.0})}
        filas, esp = mx.mercados_conteo(mh, ma, phi, mx.LINEAS_TARJETAS, mx.LINEAS_TARJETAS_EQ, "tarj", L, V)
        out["tarjetas"] = {"local": round(mh, 2), "visita": round(ma, 2), "total": round(esp, 2), "phi": round(phi, 3),
                           "media_liga": round(ex["tarj"].media_comp.get(e["lg"], 0), 2), "arbitro": info_ref,
                           "mercados": _con_cuotas(filas, ex["cal"].get("tarj", {}))}
    s = ex["props"].get(e["lg"], ex["glob"])
    out["tiempos"] = {"proporcion_1t": round(s, 3), "lh_1t": round(pred["lh"] * s, 3), "la_1t": round(pred["la"] * s, 3),
                      "mercados": _con_cuotas(mx.mercados_tiempos(pred["lh"], pred["la"], s, L, V),
                                              ex["cal"].get("tiempos", {}))}
    return out


# ------------------------------------------------------------------ jugadores y alineaciones

def preparar_jugadores(D, P, ajuste, filas_val, forzar):
    """Aporte de cada jugador, estilos y calibración (la elección de β y de goleadores, una vez al día)."""
    resumenes = alineaciones.cargar()
    if not resumenes:
        return None
    # goles esperados de base: fuera de muestra (prueba retrospectiva) cuando existen; si no, los del modelo actual
    lh, la = modelo._lambdas(P, D.h, D.a, D.c, D.neu, D.X)
    lam = {e["id"]: (float(lh[i]), float(la[i])) for i, e in enumerate(D.jugados)}
    if filas_val:
        oos = {f[9]["id"]: [f[9]["lh"], f[9]["la"]] for f in filas_val}
        escribir_json(ARCHIVO_LAMBDAS_OOS, oos)
    else:
        oos = leer_json(ARCHIVO_LAMBDAS_OOS, {})
    lam.update({k: tuple(v) for k, v in oos.items()})
    regs = jugadores.registros(D.eventos, resumenes, lam)
    cfg = ajuste.get("jugadores")
    if forzar or not cfg or cfg.get("fecha") != dt.date.today().isoformat():
        sel = jugadores.elegir_ajuste(regs, P.rho, set(oos))
        idx0 = next((i for i, x in enumerate(regs) if x["e"]["id"] in oos), len(regs))
        cfg = {"fecha": dt.date.today().isoformat(), **sel,
               "goleadores": jugadores.calibrar_goleadores(regs, sel["K"], idx0), "n_partidos": len(regs)}
        ajuste["jugadores"] = cfg
        escribir_json(C.ARCHIVO_AJUSTE, ajuste)
        log.info("Alineaciones: %s", json.dumps({k: v for k, v in cfg.items() if k != "goleadores"},
                                                ensure_ascii=False))
    est = jugadores.estilos(regs)
    grupos, _ = jugadores.agrupar(est)
    return {"J": jugadores.reproducir(regs, cfg["K"]), "cfg": cfg, "est": est, "grupos": grupos,
            "idx": jugadores.indice(regs)}


def casa_y_fuera(por_equipo, equipo, dias=365):
    """Cómo rinde el equipo de local y de visita en el último año (goles a 90')."""
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)).isoformat()
    out = {}
    for donde, lado in (("local", "h"), ("visita", "a")):
        g = e = p = gf = gc = n = 0
        for x in por_equipo.get(equipo, []):
            if x["fecha"] < limite or not x["fin"] or x[lado]["id"] != equipo or x.get("neutral"):
                continue
            m90 = datos.marcador_90(x)
            if m90 is None:
                continue
            a, b = m90 if lado == "h" else (m90[1], m90[0])
            n += 1
            gf += a
            gc += b
            g += a > b
            e += a == b
            p += a < b
        out[donde] = {"n": n, "g": g, "e": e, "p": p, "gf": round(gf / n, 2) if n else None,
                      "gc": round(gc / n, 2) if n else None, "puntos": round((3 * g + e) / n, 2) if n else None}
    return out


def construir_item(e, env):
    """Todo lo que la app muestra de un partido."""
    P, D, rng = env["P"], env["D"], env["rng"]
    mats = []
    pred = modelo.predecir(P, D, e, rng, matrices=mats)
    al = None
    jug = env.get("jug")
    if jug and e["lg"] in alineaciones.LIGAS_JUGADORES and e["estado"] in ("pre", "in"):
        try:
            conf = env["confirmadas"].get(e["id"])
            al, fh, fa = jugadores.analizar_partido(e, jug["J"], jug["cfg"], conf, (pred["lh"], pred["la"]), P.rho,
                                                    jug["est"], jug["grupos"], jug["idx"],
                                                    jug["cfg"]["goleadores"]["factor"])
            if conf and (abs(fh - 1) > 1e-9 or abs(fa - 1) > 1e-9):
                mats = []                 # la alineación confirmada mueve los goles esperados (β validado)
                pred = modelo.predecir(P, D, e, rng, matrices=mats, factores=(fh, fa))
            al["casa"] = {"h": casa_y_fuera(env["por_equipo"], e["h"]["id"]),
                          "a": casa_y_fuera(env["por_equipo"], e["a"]["id"])}
        except Exception:
            log.warning("Sin análisis de alineación para %s: %s", e["id"], traceback.format_exc(limit=3))
            al = None
    ctx = contexto(D, e, env["por_equipo"], env["tablas"])
    lu = env["lugares"].get(geo.clave(e.get("ciudad"), e.get("pais"))) or {}
    mer = prob_mercado(e.get("cuotas"))
    cl = env["clima"].get(e["id"])
    item = {
        "id": e["id"], "lg": e["lg"], "fecha": e["fecha"], "estado": e["estado"], "fin": e["fin"],
        "detalle": e["detalle"], "status": e["status"], "neutral": e["neutral"],
        "h": {k: e["h"][k] for k in ("id", "n", "c", "logo", "g")},
        "a": {k: e["a"][k] for k in ("id", "n", "c", "logo", "g")},
        "estadio": e["estadio"], "ciudad": e["ciudad"], "pais": e["pais"],
        "alt": lu.get("alt"), "tz": lu.get("tz"), "notas": e["notas"],
        "pred": pred, "disciplina": env["disc"].predecir(D, e, rng), "mercado": mer, "clima": cl, "ctx": ctx,
        "alineacion": al,
    }
    if e["estado"] == "pre" and mats:
        # matriz de marcadores para la simulación del partido en la app (todos los partidos)
        item["matriz"] = [[round(float(v), 6) for v in fila] for fila in mats[0]]
    if env.get("extras") and e["estado"] in ("pre", "in"):
        try:
            item["extras"] = construir_extras(e, pred, D, env["extras"])
        except Exception:
            log.warning("Sin córners/tarjetas/tiempos para %s: %s", e["id"], traceback.format_exc(limit=2))
    item["senales"] = señales(e, pred, ctx, cl, mer, lu.get("alt"), D)
    if al and al["estado"] == "confirmada" and (al["h"]["sale"] or al["a"]["sale"]):
        for lado in ("h", "a"):
            if al[lado]["sale"]:
                item["senales"].append({"tipo": "alineacion", "nivel": "info",
                                        "texto": f"{e[lado]['n']}: no juegan de titular {', '.join(map(str, al[lado]['sale'][:4]))} "
                                                 f"(D24.5)"})
    item["apuesta"] = None
    hist, bt = env["hist"], env["bt"]
    if e["estado"] == "pre":
        previo = (hist.get(e["id"]) or {}).get("apuesta") or {}
        previos = {k: v["cod"] for k, v in (previo.get("perfiles") or {}).items()}
        try:
            item["apuesta"] = apuestas.recomendar(mats[0], mats[1], e["h"]["n"], e["a"]["n"], e.get("cuotas"),
                                                  P.rho, pred["conf"], env["config_valor"],
                                                  excluidos=bt.get("excluidos", {}), previos=previos,
                                                  mapas=bt.get("mapas"),
                                                  kappa=bt.get("kappa", apuestas.KAPPA_INICIAL),
                                                  n_min=min(pred["calc"]["n_local"], pred["calc"]["n_visita"]),
                                                  liga_val=env["val_ligas"].get(e["lg"]))
        except Exception:
            log.warning("Sin apuesta para %s: %s", e["id"], traceback.format_exc(limit=2))
    if e["fin"] or e["estado"] == "in":
        reg = hist.get(e["id"])
        if reg and "ultima" in reg:   # se muestra lo que se predijo ANTES del partido, no lo recalculado
            item["pred_previa"] = reg["ultima"]
            if reg.get("apuesta"):
                previa = json.loads(json.dumps(reg["apuesta"]))
                m90 = datos.marcador_90(e) if e["fin"] else None
                if m90 is not None:
                    for x in previa["perfiles"].values():
                        x["res"] = modelo.liquidar(x["cod"], *m90)
                    previa["marcador_90"] = list(m90)
                item["apuesta_previa"] = previa
    return item


def ejecutar_rapido():
    """Cada 15 min: si salió la alineación de algún partido de las próximas horas, recalcula solo esos."""
    ahora = dt.datetime.now(dt.timezone.utc)
    eventos = datos.cargar_cache()
    cerca = [e for e in eventos if e["lg"] in alineaciones.LIGAS_JUGADORES and e["estado"] == "pre" and
             dt.timedelta(0) < dt.datetime.fromisoformat(e["fecha"].replace("Z", "+00:00")) - ahora
             <= dt.timedelta(hours=VENTANA_RAPIDA_H)]
    previas = leer_json(C.ARCHIVO_PREDICCIONES, {})
    if not cerca or not previas.get("partidos"):
        return 0
    ya = {p["id"] for p in previas["partidos"] if (p.get("alineacion") or {}).get("estado") == "confirmada"}
    pendientes = [e for e in cerca if e["id"] not in ya]
    if not pendientes:
        return 0
    conf = alineaciones.confirmadas(pendientes, horas=VENTANA_RAPIDA_H + 0.5)
    if not conf:
        log.info("Revisión rápida: %d partidos cerca, sin alineaciones confirmadas aún", len(pendientes))
        return 0
    estado(corriendo=True, fase=f"Alineaciones confirmadas: recalculando {len(conf)} partidos", progreso=50)
    lugares = geo.ubicar_ciudades([], maximo=0)
    D = modelo.Datos(eventos, lugares, geo.clave)
    ajuste = leer_json(C.ARCHIVO_AJUSTE, {})
    vida = ajuste.get("vida", C.VIDA_MEDIA_POR_DEFECTO)
    P = con_conjunto(D, modelo.ajustar(D, D.t < D.ahora, D.ahora, vida, iters=200), D.t < D.ahora)
    bt = ajuste.get("apuestas") or {}
    apuestas.METODO_DESMARGINADO["metodo"] = bt.get("metodo", "proporcional")
    por_equipo = {}
    for e in D.eventos:
        por_equipo.setdefault(e["h"]["id"], []).append(e)
        por_equipo.setdefault(e["a"]["id"], []).append(e)
    viejos = {p["id"]: p for p in previas["partidos"]}
    env = {"P": P, "D": D, "rng": np.random.default_rng(20261031), "disc": modelo.ModeloDisciplina(D, D.ahora, vida),
           "tablas": previas.get("tablas", {}), "lugares": lugares, "por_equipo": por_equipo,
           "clima": {k: v.get("clima") for k, v in viejos.items() if v.get("clima")}, "hist": leer_historial(),
           "bt": bt, "config_valor": {"habilitado": bool(bt.get("valor_habilitado")),
                                      "peso_modelo": bt.get("peso_modelo", 0.0)},
           "val_ligas": (ajuste.get("validacion") or {}).get("por_liga", {}), "confirmadas": conf,
           "jug": preparar_jugadores(D, P, ajuste, None, False), "extras": preparar_extras(D, vida, ajuste)}
    nuevos = []
    for e in D.eventos:
        if e["id"] in conf:
            try:
                nuevos.append(construir_item(e, env))
            except Exception:
                log.warning("Revisión rápida, sin predicción para %s: %s", e["id"], traceback.format_exc(limit=2))
    por_id = {x["id"]: x for x in nuevos}
    previas["partidos"] = [por_id.get(p["id"], p) for p in previas["partidos"]]
    previas["alineaciones_actualizadas"] = ahora_iso()
    hist = actualizar_historial(env["hist"], nuevos, {e["id"]: e for e in D.eventos})
    guardar_historial(hist)
    escribir_json(C.ARCHIVO_PREDICCIONES, previas)
    estado(corriendo=False, fase="Listo", progreso=100, ultima_ok=ahora_iso(), error=None)
    log.info("Revisión rápida: alineaciones de %d partidos", len(nuevos))
    return 0


# ------------------------------------------------------------------ principal

def ejecutar(forzar=False):
    t0 = time.time()
    estado(corriendo=True, fase="Comprobando conexión a internet", progreso=1, error=None)
    if not datos.hay_internet():
        e = leer_json(C.ARCHIVO_ESTADO, {})
        estado(corriendo=False, fase="Sin internet", progreso=0,
               error="Sin conexión. Se muestran las predicciones de la última actualización.",
               sin_internet_desde=e.get("sin_internet_desde") or ahora_iso())
        log.info("Sin internet; no se actualiza")
        return 2
    estado(sin_internet_desde=None)

    try:
        C.agregar_ligas(datos.descubrir_ligas())      # todas las competiciones masculinas de ESPN
    except Exception:
        log.warning("No se pudo actualizar la lista de ligas: %s", traceback.format_exc(limit=2))

    def av(a, b):
        estado(fase=f"Descargando resultados y calendario ({a}/{b})", progreso=2 + int(48 * a / max(b, 1)))

    eventos, errores, tareas = datos.sincronizar(av)
    log.info("Partidos en caché: %d (descargas: %d, errores: %d)", len(eventos), tareas, len(errores))
    for x in errores[:10]:
        log.warning("Descarga: %s", x)
    estado(fase="Descargando alineaciones de partidos jugados", progreso=50)
    try:
        nuevos, pend = alineaciones.sincronizar(eventos, maximo=1500)
        if pend:
            log.info("Resúmenes de partidos (alineaciones): %d de %d", nuevos, pend)
    except Exception:
        log.warning("No se pudieron bajar alineaciones: %s", traceback.format_exc(limit=2))

    estado(fase="Descargando tablas de posiciones", progreso=52)
    codigos_tabla = [c for c, _, _, nv, t in C.LIGAS if t == "liga" and nv < 4] + ["uefa.champions", "conmebol.libertadores",
                                                                  "conmebol.sudamericana", "uefa.europa",
                                                                  "uefa.europa.conf"]
    tablas = datos.bajar_tablas(codigos_tabla)
    viejas = leer_json(ARCHIVO_TABLAS, {})
    viejas.update(tablas)
    tablas = viejas
    escribir_json(ARCHIVO_TABLAS, tablas)

    estado(fase="Ubicando estadios (altitud)", progreso=56)
    hoy = dt.datetime.now(dt.timezone.utc)
    # primero los estadios de los próximos partidos, luego los más recientes
    orden = sorted((e for e in eventos if e.get("ciudad")),
                   key=lambda e: (e["estado"] == "post", -dt.datetime.fromisoformat(
                       e["fecha"].replace("Z", "+00:00")).timestamp()))
    lugares = geo.ubicar_ciudades([(e["ciudad"], e["pais"]) for e in orden], maximo=700)

    estado(fase="Preparando el modelo", progreso=62)
    D = modelo.Datos(eventos, lugares, geo.clave)
    ajuste = leer_json(C.ARCHIVO_AJUSTE, {})
    rng = np.random.default_rng(20261031)

    # elección de la vida media por validación (una vez por semana)
    fecha_aj = ajuste.get("fecha_vida")
    if forzar or not fecha_aj or (dt.date.today() - dt.date.fromisoformat(fecha_aj)).days >= 7:
        res = {}
        for i, vida in enumerate(C.VIDAS_MEDIAS_CANDIDATAS):
            estado(fase=f"Eligiendo la memoria del modelo ({i + 1}/{len(C.VIDAS_MEDIAS_CANDIDATAS)})",
                   progreso=62 + 3 * i)
            r = modelo.resumir_validacion(modelo.validar(D, vida, 10, iters=50))
            if r:
                res[str(vida)] = r["global"]
        if res:
            mejor = min(res, key=lambda k: res[k]["rps"])
            ajuste.update(vida=int(mejor), fecha_vida=dt.date.today().isoformat(), candidatas=res)
            log.info("Vida media elegida: %s días (%s)", mejor, res)
    vida = ajuste.get("vida", C.VIDA_MEDIA_POR_DEFECTO)

    # validación retrospectiva completa (una vez al día)
    filas_val = None
    fecha_val = ajuste.get("fecha_validacion", "")
    if forzar or fecha_val[:10] != dt.date.today().isoformat() or "validacion" not in ajuste \
            or "apuestas" not in ajuste or "extras" not in ajuste:
        estado(fase="Validando con partidos pasados (D24.17)", progreso=74)
        filas_val = modelo.validar(D, vida, C.SEMANAS_VALIDACION, conjunto=True)
        ajuste["validacion"] = modelo.resumir_validacion(filas_val)
        ajuste["fecha_validacion"] = ahora_iso()
        estado(fase="Probando las apuestas con cuotas reales (football-data.co.uk)", progreso=79)
        try:
            ch, resumen_ch = cuotas_hist.sincronizar(eventos)
            bt = apuestas.backtest(filas_val, ch)
            bt["emparejados"] = len(ch)
            ajuste["apuestas"] = bt
            log.info("Prueba de apuestas: %s", json.dumps({k: v for k, v in bt.items() if k != "perfiles"},
                                                         ensure_ascii=False))
            estado(fase="Probando córners, tarjetas y primer tiempo", progreso=81)
            sel_tarj = {lg for lg, cov in D.cobertura.items() if cov >= 0.7}
            cal_cor, f_cor = mx.validar_conteos(D.eventos, D.idx, D.ahora, "cor", vida=vida)
            cal_tar, f_tar = mx.validar_conteos(D.eventos, D.idx, D.ahora, "tarj", sel_comp=sel_tarj, vida=vida)
            ajuste["extras"] = {"cor": cal_cor, "tarj": cal_tar,
                                "tiempos": mx.validar_tiempos({e["id"]: e for e in D.eventos}, filas_val, D.ahora)}
            ajuste["extras_factor"] = {"cor": f_cor, "tarj": f_tar}
            log.info("Mercados extra que pasan la prueba: %s; factores de nivel %s",
                     {k: sum(1 for x in v.values() if x["ok"]) for k, v in ajuste["extras"].items()},
                     ajuste["extras_factor"])
        except Exception:
            log.warning("No se pudo probar las apuestas: %s", traceback.format_exc(limit=3))
            if not ajuste.get("apuestas"):
                # sin ninguna prueba previa: solo se permiten las opciones derivadas del 1X2, que ya está validado
                cerrados = [c for c in apuestas.CANDIDATOS if c not in ("1", "X", "2", "1X", "X2", "DNB1", "DNB2")]
                ajuste["apuestas"] = {"error": True, "excluidos": {"modelo": cerrados, "mercado": cerrados}}
    escribir_json(C.ARCHIVO_AJUSTE, ajuste)
    bt = ajuste.get("apuestas") or {}
    config_valor = {"habilitado": bool(bt.get("valor_habilitado")), "peso_modelo": bt.get("peso_modelo", 0.0)}
    apuestas.METODO_DESMARGINADO["metodo"] = bt.get("metodo", "proporcional")
    val_ligas = (ajuste.get("validacion") or {}).get("por_liga", {})

    estado(fase="Calculando fuerzas de los equipos", progreso=84)
    jugados = D.t < D.ahora
    P = con_conjunto(D, modelo.ajustar(D, jugados, D.ahora, vida, iters=200), jugados)
    disc = modelo.ModeloDisciplina(D, D.ahora, vida)

    # partidos a mostrar
    desde = (hoy - dt.timedelta(days=C.DIAS_RESULTADOS)).isoformat()
    hasta = (hoy + dt.timedelta(days=C.DIAS_PROXIMOS)).isoformat()
    mostrar = [e for e in D.eventos if desde <= e["fecha"] <= hasta]
    proximos = [e for e in mostrar if e["estado"] in ("pre", "in")]
    estado(fase="Consultando el clima de los estadios", progreso=88)
    clima = geo.clima_partidos(proximos, lugares)

    por_equipo = {}
    for e in D.eventos:
        por_equipo.setdefault(e["h"]["id"], []).append(e)
        por_equipo.setdefault(e["a"]["id"], []).append(e)

    estado(fase="Alineaciones y aporte de cada jugador", progreso=90)
    jug = None
    try:
        jug = preparar_jugadores(D, P, ajuste, filas_val, forzar)
    except Exception:
        log.warning("Sin análisis de jugadores: %s", traceback.format_exc(limit=3))
    confirmadas = alineaciones.confirmadas([e for e in proximos if e["lg"] in alineaciones.LIGAS_JUGADORES], horas=3)
    extras = None
    try:
        extras = preparar_extras(D, vida, ajuste)
    except Exception:
        log.warning("Sin córners/tarjetas/tiempos: %s", traceback.format_exc(limit=3))

    estado(fase="Calculando predicciones", progreso=93)
    hist = leer_historial()
    env = {"P": P, "D": D, "rng": rng, "disc": disc, "tablas": tablas, "lugares": lugares, "por_equipo": por_equipo,
           "clima": clima, "hist": hist, "bt": bt, "config_valor": config_valor, "val_ligas": val_ligas,
           "confirmadas": confirmadas, "jug": jug, "extras": extras}
    salida = []
    for e in mostrar:
        try:
            salida.append(construir_item(e, env))
        except Exception:
            log.warning("No se pudo predecir %s: %s", e["id"], traceback.format_exc(limit=2))

    hist = actualizar_historial(hist, salida, {e["id"]: e for e in D.eventos})
    guardar_historial(hist)

    # resumen del modelo para la pestaña "Modelo"
    comps_info = {}
    for c in D.comps:
        ci = D.cidx[c]
        n_c = int(((D.c == ci) & (D.t > D.ahora - 365)).sum())
        comps_info[c] = {"H": round(float(P.Hh[ci]), 3), "V": round(float(P.Ha[ci]), 3), "n_365": n_c,
                         "cobertura_tarjetas": round(D.cobertura.get(c, 0), 2)}
    ranking = []
    for i, q in enumerate(D.equipos):
        if P.expo[i] >= 8:
            fuerza = math.log(P.att[i]) - math.log(P.dfn[i])
            ranking.append({"n": q["n"], "logo": q["logo"], "grupo": D.grupo_nombre[i],
                            "A": round(float(P.att[i]), 3), "D": round(float(P.dfn[i]), 3),
                            "fuerza": round(fuerza, 3), "n_ef": round(float(P.expo[i]), 1)})
    ranking.sort(key=lambda r: -r["fuerza"])

    hist_lista = sorted([dict(id=k, **v) for k, v in hist.items() if v.get("res")],
                        key=lambda v: v["fecha"], reverse=True)[:400]
    res = {
        "generado": ahora_iso(), "vida_media": vida,
        "ligas": [C.INFO_LIGA[c] for c, *_ in C.LIGAS],
        "partidos": salida, "tablas": tablas,
        "modelo": {
            "mu": round(P.mu, 4), "rho": P.rho, "coef": {modelo.NOMBRE_COEF[k]: round(v, 4) for k, v in P.b.items()},
            "competiciones": comps_info, "validacion": ajuste.get("validacion"),
            "fecha_validacion": ajuste.get("fecha_validacion"), "candidatas": ajuste.get("candidatas"),
            "n_partidos": int(jugados.sum()), "n_equipos": D.nT, "ranking": ranking[:300],
            "semanas_validacion": C.SEMANAS_VALIDACION,
            "estadios_ubicados": len(lugares),
        },
        "historial": {"resumen": resumen_historial(hist), "lista": hist_lista},
        "apuestas": {
            "perfiles": {k: {"nombre": v["nombre"], "rango": v["rango"], "texto": v["texto"], "tope": v["tope"]}
                         for k, v in apuestas.PERFILES.items()},
            "por_defecto": apuestas.PERFIL_POR_DEFECTO, "kelly": apuestas.KELLY_FRACCION,
            "prueba": {k: v for k, v in (ajuste.get("apuestas") or {}).items() if k != "mapas"},
            "fecha_prueba": ajuste.get("fecha_validacion"),
        },
        "jugadores": {k: v for k, v in (jug or {}).get("cfg", {}).items()} if jug else None,
        "ligas_alineacion": alineaciones.LIGAS_JUGADORES,
        "fuentes": [
            {"nombre": "ESPN (site.api.espn.com)", "uso": "resultados, calendario, tarjetas, penales, estadios, "
                                                          "tablas, cuotas de DraftKings, alineaciones y "
                                                          "estadísticas por jugador", "fecha": ahora_iso()},
            {"nombre": "Open-Meteo (open-meteo.com)", "uso": "ubicación y altitud de las ciudades, pronóstico del "
                                                             "clima", "fecha": ahora_iso()},
        ],
    }
    escribir_json(C.ARCHIVO_PREDICCIONES, res)
    seg = round(time.time() - t0)
    estado(corriendo=False, fase="Listo", progreso=100, ultima_ok=ahora_iso(), duracion_s=seg, error=None,
           partidos=len(salida))
    log.info("Listo en %ss: %d partidos", seg, len(salida))
    return 0


def main():
    configurar_log()
    candado = tomar_candado()
    if candado is None:
        log.info("Ya hay una actualización en curso")
        return 0
    try:
        if "--rapido" in sys.argv:
            return ejecutar_rapido()
        return ejecutar(forzar="--forzar" in sys.argv)
    except datos.SinInternet:
        estado(corriendo=False, fase="Sin internet", progreso=0,
               error="No se pudo conectar con ESPN. Se muestran las predicciones de la última actualización.")
        return 2
    except Exception as ex:
        log.error("Error: %s", traceback.format_exc())
        estado(corriendo=False, fase="Error", progreso=0, error=f"{type(ex).__name__}: {ex}")
        return 1
    finally:
        candado.close()


if __name__ == "__main__":
    sys.exit(main())
