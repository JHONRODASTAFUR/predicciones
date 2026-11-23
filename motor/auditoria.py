"""Auditoría permanente y registro de los tres momentos de cada pronóstico.

Qué hace, en simple:
  · registrar(): en cada actualización guarda, por partido, la foto del pronóstico en tres momentos
    (24-48 h antes, 3-6 h antes y con la alineación oficial) y anota QUÉ CAMBIÓ Y POR QUÉ entre uno y otro.
    También guarda la primera cuota y la última (cierre) para medir el CLV.
  · resumen(): con todos los partidos ya jugados mide, sin adornos, cómo le fue al programa: calibración, sesgos,
    ligas donde falla, acierto y rendimiento de cada tipo de apuesta, por clase, por rango de cuota y por semana.
  · Las ligas donde el modelo no supera a la referencia simple pasan a «abstención» cuando no hay cuotas.

Todo se calcula con predicciones guardadas ANTES de cada partido: no hay forma de mirar el futuro.
CLV (valor frente al cierre) = cuota cuando se recomendó ÷ cuota al cierre − 1, con la casa de referencia de ESPN
(no Betano: sus cuotas no se leen).
"""
import collections
import datetime as dt
import math

ORDEN_MOMENTOS = ("48h", "6h", "oficial")
NOMBRE_MOMENTO = {"48h": "24-48 h antes", "6h": "3-6 h antes", "oficial": "con alineación oficial"}
MIN_LIGA = 25                 # partidos mínimos para opinar de una liga
MIN_ABSTENCION = 30
RANGOS_CUOTA = ((1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 99.0))


def _t(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _n(x):
    return f"{x:.2f}".replace(".", ",")


# ------------------------------------------------------------------ registro por partido

def _cambio(de, f0, a, f1):
    d = {k: round(f1[k] - f0[k], 4) for k in ("pH", "pD", "pA")}
    motivos = []
    c0, c1 = f0.get("cuotas"), f1.get("cuotas")
    if c0 and c1 and c0 != c1:
        motivos.append(f"las cuotas pasaron de {' / '.join(map(_n, c0))} a {' / '.join(map(_n, c1))}")
    elif c1 and not c0:
        motivos.append("aparecieron cuotas del mercado (antes solo opinaba el modelo)")
    elif c0 and not c1:
        motivos.append("ESPN retiró las cuotas")
    if f1.get("al") == "confirmada" and f0.get("al") != "confirmada":
        fuera = [n for n in f1.get("sale") or [] if n]
        motivos.append("se confirmó la alineación oficial" + (f": no son titulares {', '.join(fuera[:4])}" if fuera else
                                                               " sin cambios frente al once habitual"))
    nuevas = [n for n in f1.get("bajas") or [] if n not in (f0.get("bajas") or [])]
    if nuevas:
        motivos.append(f"noticias de posible baja: {', '.join(nuevas[:3])}")
    grande = max(abs(v) for v in d.values()) >= 0.01
    if not motivos:
        motivos.append("nuevos resultados y recálculo del modelo" if grande else "sin cambios relevantes")
    return {"de": de, "a": a, "t": f1["guardado"], "dH": d["pH"], "dD": d["pD"], "dA": d["pA"], "motivos": motivos}


def registrar(reg, item, ahora):
    """Completa el registro del historial de un partido que aún no empieza."""
    p, mer, al, ap = item["pred"], item.get("mercado"), item.get("alineacion") or {}, item.get("apuesta")
    cuotas = None
    if mer and mer.get("cuotas"):
        cuotas = [mer["cuotas"][k] for k in ("h", "x", "a")]
        reg.setdefault("cuotas_ini", cuotas)
        reg["cuotas_fin"] = cuotas
        reg.setdefault("mercado_ini", {k: mer[k] for k in ("pH", "pD", "pA")})
    if p.get("solo_modelo"):
        reg.setdefault("modelo_ini", {k: p["solo_modelo"][k] for k in ("pH", "pD", "pA")})
    if ap:
        por_cod = {f["cod"]: f for f in ap["mercados"]}
        ini = reg.setdefault("apuesta_ini", {k: {"cod": x["cod"], "p": por_cod[x["cod"]]["p"],
                                                 "cuota": por_cod[x["cod"]].get("cuota_casa")}
                                             for k, x in ap["perfiles"].items() if x["cod"] in por_cod})
        for x in ini.values():                    # la última cuota vista de esa misma selección = cierre
            f = por_cod.get(x["cod"])
            if f and f.get("cuota_casa"):
                x["cierre"] = f["cuota_casa"]
        clases = (item.get("intel") or {}).get("perfiles") or {}
        for k, x in ((reg.get("apuesta") or {}).get("perfiles") or {}).items():
            if k in clases and clases[k]["cod"] == x["cod"]:
                x["clase"], x["evidencia"] = clases[k]["clase"], clases[k]["evidencia"]
    # los tres momentos
    horas = (_t(item["fecha"]) - ahora).total_seconds() / 3600
    nombre = "oficial" if al.get("estado") == "confirmada" else "6h" if horas <= 6 else "48h" if horas <= 48 else None
    if not nombre:
        return
    foto = {"pH": p["pH"], "pD": p["pD"], "pA": p["pA"], "lh": p["lh"], "la": p["la"],
            "guardado": ahora.isoformat(timespec="seconds"), "cuotas": cuotas, "al": al.get("estado"),
            "bajas": [j["n"] for s in ("h", "a") for j in (al.get(s) or {}).get("jugadores") or [] if j.get("posible_baja")]}
    if nombre == "oficial":
        foto["sale"] = [n for s in ("h", "a") for n in (al.get(s) or {}).get("sale") or []]
    ms = reg.setdefault("momentos", {})
    ms[nombre] = foto
    previos = [m for m in ORDEN_MOMENTOS[:ORDEN_MOMENTOS.index(nombre)] if m in ms]
    if previos:
        cambios = [c for c in reg.get("cambios", []) if c["a"] != nombre]
        cambios.append(_cambio(previos[-1], ms[previos[-1]], nombre, foto))
        reg["cambios"] = cambios


def liquidar(reg, yh, ya, liquidar_mercado):
    """Al terminar el partido: resultado de las apuestas recomendadas la primera vez (para el CLV)."""
    for x in (reg.get("apuesta_ini") or {}).values():
        x["res"] = liquidar_mercado(x["cod"], yh, ya)


# ------------------------------------------------------------------ medidas

def _y(v):
    a, b = v["res"][:2]
    return 0 if a > b else 1 if a == b else 2


def _met(filas, prob):
    """RPS, Brier, log-loss y acierto del 1X2. `prob(v)` devuelve {pH, pD, pA} o None."""
    n = rps = bs = ll = ac = 0
    for v in filas:
        p = prob(v)
        if not p:
            continue
        q, o = [p["pH"], p["pD"], p["pA"]], _y(v)
        c1, c2 = q[0] - (o == 0), q[0] + q[1] - (o <= 1)
        rps += (c1 * c1 + c2 * c2) / 2
        bs += sum((q[i] - (i == o)) ** 2 for i in range(3))
        ll += -math.log(max(q[o], 1e-9))
        ac += q.index(max(q)) == o
        n += 1
    if not n:
        return None
    return {"n": n, "rps": round(rps / n, 4), "brier": round(bs / n, 4), "logloss": round(ll / n, 4), "acierto": round(ac / n, 4)}


def _roi(xs):
    con = [x for x in xs if x.get("cuota_casa") and x["res"] != "devuelve"]
    if not con:
        return None, 0
    return round(sum((x["cuota_casa"] - 1) if x["res"] == "gana" else -1 for x in con) / len(con), 4), len(con)


def _grupo(xs):
    j = [x for x in xs if x["res"] != "devuelve"]
    if not j:
        return None
    roi, n_cuota = _roi(xs)
    return {"n": len(j), "acierto": round(sum(x["res"] == "gana" for x in j) / len(j), 4),
            "esperado": round(sum(x["p"] for x in j) / len(j), 4), "roi": roi, "n_cuota": n_cuota}


def resumen(hist):
    """Auditoría de precisión con todo el historial ya jugado."""
    R = [v for v in hist.values() if v.get("res") and v.get("ultima")]
    n = len(R)
    if n < 50:
        return {"n": n}
    ult = lambda v: v["ultima"]                    # noqa: E731
    fr = collections.Counter(_y(v) for v in R)
    base = {"pH": fr[0] / n, "pD": fr[1] / n, "pA": fr[2] / n}
    Rm = [v for v in R if v.get("mercado")]
    out = {"n": n, "desde": min(v["fecha"] for v in R)[:10], "hasta": max(v["fecha"] for v in R)[:10],
           "ultima": _met(R, ult), "primera": _met(R, lambda v: v.get("primera")), "ingenuo": _met(R, lambda v: base),
           "con_cuotas": {"modelo": _met(Rm, ult), "mercado": _met(Rm, lambda v: v["mercado"])},
           "sin_cuotas": _met([v for v in R if not v.get("mercado")], ult)}

    # sesgos: lo predicho de media frente a lo que pasó (z = desvío en errores estándar; |z| > 2 merece atención)
    def sesgo(nombre, pred, real):
        ee = math.sqrt(max(real * (1 - real), 1e-9) / n)
        return {"n": nombre, "pred": round(pred, 4), "real": round(real, 4), "z": round((real - pred) / ee, 1)}
    out["sesgos"] = [
        sesgo("Gana el local", sum(v["ultima"]["pH"] for v in R) / n, base["pH"]),
        sesgo("Empate", sum(v["ultima"]["pD"] for v in R) / n, base["pD"]),
        sesgo("Gana el visitante", sum(v["ultima"]["pA"] for v in R) / n, base["pA"]),
        sesgo("Más de 2,5 goles", sum(v["ultima"]["o25"] for v in R) / n, sum(sum(v["res"][:2]) > 2.5 for v in R) / n),
        sesgo("Ambos marcan", sum(v["ultima"]["btts"] for v in R) / n, sum(v["res"][0] > 0 and v["res"][1] > 0 for v in R) / n),
    ]
    # ¿se sobrevalora al favorito?
    tramos = collections.defaultdict(list)
    for v in R:
        q = [v["ultima"]["pH"], v["ultima"]["pD"], v["ultima"]["pA"]]
        tramos[min(int(max(q) * 10), 8)].append((max(q), q.index(max(q)) == _y(v), q.index(max(q))))
    out["favorito"] = [{"desde": b / 10, "n": len(x), "pred": round(sum(a for a, _, _ in x) / len(x), 4),
                        "real": round(sum(c for _, c, _ in x) / len(x), 4)} for b, x in sorted(tramos.items())]
    todos = [x for xs in tramos.values() for x in xs]
    out["local_visita"] = {nombre: {"n": len(x), "pred": round(sum(a for a, _, _ in x) / len(x), 4),
                                    "real": round(sum(c for _, c, _ in x) / len(x), 4)}
                           for nombre, x in (("favorito local", [t for t in todos if t[2] == 0]),
                                             ("favorito visitante", [t for t in todos if t[2] == 2])) if x}
    # ligas
    por_liga = collections.defaultdict(list)
    for v in R:
        por_liga[v["lg"]].append(v)
    ligas = []
    for lg, x in por_liga.items():
        if len(x) >= MIN_LIGA:
            m, ing = _met(x, ult), _met(x, lambda v: base)
            ligas.append({"lg": lg, "n": m["n"], "rps": m["rps"], "rps_ingenuo": ing["rps"], "acierto": m["acierto"],
                          "con_cuotas": sum(1 for v in x if v.get("mercado"))})
    ligas.sort(key=lambda z: z["rps_ingenuo"] - z["rps"])
    out["ligas"] = {"medidas": len(ligas), "peores": ligas[:8], "mejores": ligas[-5:][::-1]}
    out["abstener"] = sorted(z["lg"] for z in ligas if z["n"] >= MIN_ABSTENCION and z["rps"] >= z["rps_ingenuo"])
    # apuestas
    perfiles = {}
    for k in ("conservador", "equilibrado", "arriesgado"):
        xs = [v["apuesta"]["perfiles"][k] for v in R if ((v.get("apuesta") or {}).get("perfiles") or {}).get(k, {}).get("res")]
        g = _grupo(xs)
        if not g:
            continue
        por = lambda campo: {c: _grupo([x for x in xs if x.get(campo) == c]) for c in sorted({x.get(campo) for x in xs if x.get(campo)})}  # noqa: E731
        g["por_riesgo"], g["por_clase"] = por("riesgo"), por("clase")
        g["por_rango"] = [dict(_grupo(z), rango=[a, b]) for a, b in RANGOS_CUOTA
                          for z in [[x for x in xs if a <= x["justa"] < b]] if _grupo(z)]
        perfiles[k] = g
    out["perfiles"] = perfiles
    # CLV de las recomendaciones iniciales y movimiento del mercado hacia el modelo
    clv = [x["cuota"] / x["cierre"] - 1 for v in R for x in (v.get("apuesta_ini") or {}).values()
           if x.get("cuota") and x.get("cierre")]
    out["clv"] = {"n": len(clv), "medio": round(sum(clv) / len(clv), 4) if clv else None,
                  "positivo": round(sum(c > 0 for c in clv) / len(clv), 4) if clv else None}
    hacia = []
    for v in R:
        m0, f0, f1 = v.get("modelo_ini"), v.get("mercado_ini"), v.get("mercado")
        if m0 and f0 and f1:
            k = max(("pH", "pD", "pA"), key=lambda q: abs(m0[q] - f0[q]))
            d0, mv = m0[k] - f0[k], f1[k] - f0[k]
            if abs(d0) >= 0.03 and abs(mv) >= 0.01:
                hacia.append(d0 * mv > 0)
    out["hacia_modelo"] = {"n": len(hacia), "pct": round(sum(hacia) / len(hacia), 4) if hacia else None}
    # los tres momentos y la evolución por semanas
    out["momentos"] = {m: _met(R, lambda v, m=m: (v.get("momentos") or {}).get(m)) for m in ORDEN_MOMENTOS}
    semanas = collections.defaultdict(list)
    for v in R:
        y, w, _ = dt.date.fromisoformat(v["fecha"][:10]).isocalendar()
        semanas[f"{y}-S{w:02d}"].append(v)
    out["semanas"] = [dict(_met(x, ult), semana=s) for s, x in sorted(semanas.items())[-8:]]
    return out
