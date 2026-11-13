"""Aporte de cada jugador, alineaciones, goleadores y estilo de juego (D24.5, D12, D15, D25).

Todo se calcula en orden cronológico: lo que se sabe de un jugador antes de un partido sale SOLO de los partidos
anteriores. La línea base de cada partido son los goles esperados del modelo (fuera de muestra cuando existe).

- Aporte de un jugador (ataque): goles de su equipo por encima de lo esperado mientras él está en cancha,
  ponderados por sus minutos y contraídos hacia 0 con K goles esperados de "prior". Igual en defensa (goles
  recibidos por encima de lo esperado; positivo = peor).
- Ajuste por alineación: se compara el once confirmado con el once habitual del equipo. El peso β del ajuste se
  elige con la mitad antigua de la prueba y se verifica con la reciente; si no mejora, β = 0 (no se usa).
- Goleadores: parte de los goles del equipo que suele hacer cada jugador (con un prior por posición) × goles
  esperados del equipo × minutos esperados. Su calibración se mide y se corrige con un factor.
- Estilo: promedio de posesión, pases, precisión, remates, centros, juego directo y presión de los últimos 10
  partidos; los equipos se agrupan en 4 estilos (k-medias) para ver cómo rinden contra rivales parecidos.
"""
import datetime as dt
import math
from collections import defaultdict

import numpy as np

from .alineaciones import linea
from .modelo import matriz, resumen_matriz

PRIOR_POS = {"DEL": 0.20, "MED": 0.08, "DEF": 0.035, "POR": 0.0}
MIN_TITULAR = 0.82            # fracción de minutos esperada de un titular
TOPE_AJUSTE = 0.25            # el ajuste por alineación nunca mueve los goles esperados más de ±25%
REJILLA_K = [15.0, 30.0, 60.0]
REJILLA_BETA = [0.0, 0.25, 0.5, 0.75, 1.0]
VARIABLES_ESTILO = ["posesion", "pases", "precision", "remates", "centros", "directo", "presion", "despejes"]
NOMBRE_ESTILO = {"posesion": "Posesión y elaboración", "directo": "Juego directo", "centros": "Juego por bandas",
                 "despejes": "Bloque bajo", "presion": "Presión y recuperación", "remates": "Ofensivo de remate",
                 "pases": "Posesión y elaboración", "precision": "Posesión y elaboración"}


def _fecha(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class Jugadores:
    """Acumula, partido a partido, el aporte de cada jugador y lo que hace falta para predecir."""

    def __init__(self, K=30.0):
        self.K = K
        self.att_num = defaultdict(float)
        self.att_den = defaultdict(float)
        self.def_num = defaultdict(float)
        self.def_den = defaultdict(float)
        self.goles = defaultdict(float)
        self.remates = defaultdict(float)
        self.arco = defaultdict(float)
        self.asist = defaultdict(float)
        self.minutos = defaultdict(float)
        self.goles_equipo_en_cancha = defaultdict(float)
        self.info = {}                                   # id -> {n, pos, linea, equipo}
        self.posiciones = defaultdict(lambda: defaultdict(int))
        self.ultimos = defaultdict(list)                 # equipo -> últimos partidos [{id: minutos}]

    def att(self, j):
        return self.att_num[j] / (self.att_den[j] + self.K)

    def dfn(self, j):
        return self.def_num[j] / (self.def_den[j] + self.K)

    def habitual(self, equipo, n=8):
        """Once habitual: los 11 con más minutos en los últimos n partidos del equipo."""
        mins = defaultdict(float)
        for p in self.ultimos[equipo][-n:]:
            for j, m in p.items():
                mins[j] += m
        return [j for j, _ in sorted(mins.items(), key=lambda x: -x[1])[:11]]

    def delta(self, equipo, once):
        hab = self.habitual(equipo)
        if len(hab) < 11 or len(once) < 11:
            return 0.0, 0.0
        da = sum(self.att(j) for j in once) - sum(self.att(j) for j in hab)
        dd = sum(self.dfn(j) for j in once) - sum(self.dfn(j) for j in hab)
        return da, dd

    def cuota_goles(self, j):
        """Parte de los goles del equipo que hace el jugador mientras está en cancha (con prior por posición)."""
        pos = (self.info.get(j) or {}).get("linea", "MED")
        c = 4.0
        return (self.goles[j] + c * PRIOR_POS.get(pos, 0.08)) / (self.goles_equipo_en_cancha[j] + c)

    def actualizar(self, equipo, jugadores, gf, ga, lf, la):
        """Suma un partido jugado (gf/ga reales a 90', lf/la esperados)."""
        part = {}
        for p in jugadores:
            j, m = p["id"], p.get("min", 0) / 90.0
            if m <= 0:
                continue
            pos = (p.get("pos") or "").upper()
            if pos and pos != "SUB":                        # "SUB" = entró desde el banco: no es su posición
                self.posiciones[j][pos] += 1
            habitual = max(self.posiciones[j].items(), key=lambda x: x[1])[0] if self.posiciones[j] else "M"
            self.info[j] = {"n": p.get("n"), "pos": habitual, "linea": linea(habitual), "equipo": equipo}
            part[j] = p.get("min", 0)
            if lf is not None:
                self.att_num[j] += m * (gf - lf)
                self.att_den[j] += m * lf
                self.def_num[j] += m * (ga - la)
                self.def_den[j] += m * la
            self.goles[j] += p.get("g", 0)
            self.remates[j] += p.get("sh", 0)
            self.arco[j] += p.get("sot", 0)
            self.asist[j] += p.get("a", 0)
            self.minutos[j] += p.get("min", 0)
            self.goles_equipo_en_cancha[j] += gf * m
        self.ultimos[equipo].append(part)
        if len(self.ultimos[equipo]) > 20:
            self.ultimos[equipo] = self.ultimos[equipo][-20:]


def reproducir(regs, K):
    """Estado actual de todos los jugadores, sumando todos los partidos en orden."""
    J = Jugadores(K)
    for x in regs:
        lam = x["lam"]
        lf, lc = (lam[0], lam[1]) if lam is not None else (None, None)
        eh, ea = x["r"]["equipos"]["h"], x["r"]["equipos"]["a"]
        J.actualizar(eh["id"], eh["jugadores"], x["gh"], x["ga"], lf, lc)
        J.actualizar(ea["id"], ea["jugadores"], x["ga"], x["gh"], lc, lf)
    return J


def registros(eventos, resumenes, lambdas):
    """Partidos jugados con resumen, en orden cronológico, con goles a 90' y goles esperados de base."""
    from .datos import marcador_90
    out = []
    for e in sorted(eventos, key=lambda x: x["fecha"]):
        r = resumenes.get(e["id"])
        if not r or not e.get("fin"):
            continue
        m90 = marcador_90(e)
        if m90 is None:
            continue
        lam = lambdas.get(e["id"])
        out.append({"e": e, "r": r, "gh": m90[0], "ga": m90[1], "lam": lam})
    return out


def _recorrer(regs, K, desde_idx):
    """Recorre los partidos en orden. Para cada partido desde desde_idx (con goles esperados fuera de muestra)
    guarda, ANTES de sumarlo, la diferencia de su once frente al habitual. Devuelve filas (i, lh, la, sh, sa, real)
    donde sh/sa son las sumas que multiplican a β en los goles del local y del visitante."""
    J = Jugadores(K)
    filas = []
    for i, x in enumerate(regs):
        eh, ea = x["r"]["equipos"]["h"], x["r"]["equipos"]["a"]
        lam = x["lam"]
        if i >= desde_idx and lam is not None:
            dah, ddh = J.delta(eh["id"], [p["id"] for p in eh["jugadores"] if p.get("tit")])
            daa, dda = J.delta(ea["id"], [p["id"] for p in ea["jugadores"] if p.get("tit")])
            real = 0 if x["gh"] > x["ga"] else 1 if x["gh"] == x["ga"] else 2
            filas.append((i, lam[0], lam[1], dah + dda, daa + ddh, real))
        lf, lc = (lam[0], lam[1]) if lam is not None else (None, None)
        J.actualizar(eh["id"], eh["jugadores"], x["gh"], x["ga"], lf, lc)
        J.actualizar(ea["id"], ea["jugadores"], x["ga"], x["gh"], lc, lf)
    return filas


def _logloss(filas, beta, rho):
    if not filas:
        return 0.0
    f = np.array(filas)
    fh = np.exp(np.clip(beta * f[:, 3], -TOPE_AJUSTE, TOPE_AJUSTE))
    fa = np.exp(np.clip(beta * f[:, 4], -TOPE_AJUSTE, TOPE_AJUSTE))
    pH, pD, pA, _, _ = resumen_matriz(matriz(f[:, 1] * fh, f[:, 2] * fa, rho))
    P = np.stack([pH, pD, pA], axis=1)
    return float(-np.log(np.clip(P[np.arange(len(f)), f[:, 5].astype(int)], 1e-9, 1)).mean())


def elegir_ajuste(regs, rho, ids_oos):
    """β y K: se eligen con la mitad antigua de los partidos con goles esperados fuera de muestra y se verifican
    con la mitad reciente. Si no mejora el log-loss en la verificación, β = 0 (la alineación solo se muestra)."""
    idx_oos = [i for i, x in enumerate(regs) if x["e"]["id"] in ids_oos]
    if len(idx_oos) < 400:
        return {"beta": 0.0, "K": 30.0, "motivo": "pocos partidos para probarlo"}
    mitad = idx_oos[len(idx_oos) // 2]
    resultados = []
    for K in REJILLA_K:
        filas = [f for f in _recorrer(regs, K, idx_oos[0]) if regs[f[0]]["e"]["id"] in ids_oos]
        eleccion = [f for f in filas if f[0] < mitad]
        verif = [f for f in filas if f[0] >= mitad]
        base = _logloss(eleccion, 0.0, rho)
        for beta in REJILLA_BETA[1:]:
            resultados.append((_logloss(eleccion, beta, rho) - base, K, beta, verif, len(eleccion)))
    mejora, K, beta, verif, n_el = min(resultados, key=lambda r: r[0])
    l_con, l_sin = _logloss(verif, beta, rho), _logloss(verif, 0.0, rho)
    ok = mejora < 0 and l_con < l_sin
    return {"beta": beta if ok else 0.0, "K": K, "beta_probado": beta, "n_eleccion": n_el,
            "logloss_con": round(l_con, 5), "logloss_sin": round(l_sin, 5), "n_verificacion": len(verif),
            "motivo": "mejora en partidos que no se usaron para elegirlo" if ok else
            "no mejoró en la verificación: la alineación se muestra, pero no cambia las probabilidades"}


def calibrar_goleadores(regs, K, desde_idx):
    """Probabilidad de que cada titular marque (antes del partido) frente a lo que pasó. Devuelve el factor que
    iguala el total esperado al real en la primera mitad y la calibración en la segunda."""
    J = Jugadores(K)
    pred = []
    for i, x in enumerate(regs):
        lam = x["lam"]
        for lado, otro, gf in (("h", "a", x["gh"]), ("a", "h", x["ga"])):
            eq = x["r"]["equipos"][lado]
            if i >= desde_idx and lam is not None:
                lf = lam[0] if lado == "h" else lam[1]
                for p in eq["jugadores"]:
                    if not p.get("tit") or linea(p.get("pos")) == "POR":
                        continue
                    if J.minutos[p["id"]] < 270:          # sin historia suficiente no se predice
                        continue
                    xg = lf * J.cuota_goles(p["id"]) * MIN_TITULAR
                    pred.append((i, xg, 1 if p.get("g", 0) > 0 else 0))
        if lam is not None:
            for lado, gf, ga, lf, la in (("h", x["gh"], x["ga"], lam[0], lam[1]), ("a", x["ga"], x["gh"], lam[1], lam[0])):
                J.actualizar(x["r"]["equipos"][lado]["id"], x["r"]["equipos"][lado]["jugadores"], gf, ga, lf, la)
    if len(pred) < 1000:
        return {"factor": 1.0, "n": len(pred), "calibracion": []}
    mitad = len(pred) // 2
    xs = np.array([p[1] for p in pred])
    ys = np.array([p[2] for p in pred])
    # factor para que la probabilidad media coincida con la frecuencia real (aprendido con la primera mitad)
    lo, hi = 0.3, 3.0
    for _ in range(50):
        f = (lo + hi) / 2
        if (1 - np.exp(-f * xs[:mitad])).mean() < ys[:mitad].mean():
            lo = f
        else:
            hi = f
    factor = (lo + hi) / 2
    pv = 1 - np.exp(-factor * xs[mitad:])
    yv = ys[mitad:]
    cal = []
    for a, b in ((0, .1), (.1, .2), (.2, .3), (.3, .45), (.45, 1)):
        m = (pv >= a) & (pv < b)
        if m.sum() >= 30:
            cal.append({"desde": a, "hasta": b, "pred": round(float(pv[m].mean()), 4),
                        "real": round(float(yv[m].mean()), 4), "n": int(m.sum())})
    return {"factor": round(factor, 4), "n": int(len(yv)), "pred_media": round(float(pv.mean()), 4),
            "real_media": round(float(yv.mean()), 4), "calibracion": cal}


# ---------------------------------------------------------------- análisis de un partido

def analizar_partido(e, J, conf, confirmada, lam, rho, estilos_eq, grupos, idx, factor_gol):
    """Sección de alineación de un partido. lam = (λ local, λ visita) del modelo. Devuelve (dict, fh, fa)."""
    beta = conf.get("beta", 0.0)
    lados = {}
    for lado, eq in (("h", e["h"]), ("a", e["a"])):
        hab = J.habitual(eq["id"])
        if confirmada and confirmada.get(lado):
            once = [p["id"] for p in confirmada[lado]["jugadores"]]
            for p in confirmada[lado]["jugadores"]:      # jugadores nuevos que aún no tienen historia
                J.info.setdefault(p["id"], {"n": p["n"], "pos": p["pos"], "linea": linea(p["pos"]),
                                            "equipo": eq["id"]})
                if p["pos"] and p["pos"].upper() != "SUB":
                    J.info[p["id"]]["pos_hoy"] = p["pos"]
            formacion = confirmada[lado].get("formacion")
        else:
            once, formacion = hab, None
        lados[lado] = {"once": once, "hab": hab, "formacion": formacion}
    dah, ddh = J.delta(e["h"]["id"], lados["h"]["once"]) if confirmada else (0.0, 0.0)
    daa, dda = J.delta(e["a"]["id"], lados["a"]["once"]) if confirmada else (0.0, 0.0)
    fh = math.exp(max(-TOPE_AJUSTE, min(TOPE_AJUSTE, beta * (dah + dda))))
    fa = math.exp(max(-TOPE_AJUSTE, min(TOPE_AJUSTE, beta * (daa + ddh))))
    lh, la = lam[0] * fh, lam[1] * fa
    salida = {"estado": "confirmada" if confirmada else "probable", "beta": beta,
              "factor_local": round(fh, 4), "factor_visita": round(fa, 4), "lh": round(lh, 3), "la": round(la, 3)}
    goleadores = []
    for lado, rival, lam_eq, d_att, d_def in (("h", "a", lh, dah, ddh), ("a", "h", la, daa, dda)):
        eq = e[lado]
        filas, lineas = [], defaultdict(lambda: {"ataque": 0.0, "defensa": 0.0, "n": 0})
        for j in lados[lado]["once"]:
            inf = J.info.get(j, {})
            pos = inf.get("pos_hoy") or inf.get("pos") or ""
            ln = linea(pos)
            mins = J.minutos[j]
            r = {"id": j, "n": inf.get("n") or "?", "pos": pos, "linea": ln,
                 "ataque": round(J.att(j), 3), "defensa": round(J.dfn(j), 3), "minutos": int(mins),
                 "g90": round(J.goles[j] / mins * 90, 2) if mins >= 90 else None,
                 "a90": round(J.asist[j] / mins * 90, 2) if mins >= 90 else None,
                 "rem90": round(J.remates[j] / mins * 90, 2) if mins >= 90 else None}
            if ln != "POR":
                xg = factor_gol * lam_eq * J.cuota_goles(j) * MIN_TITULAR
                r["prob_gol"] = round(1 - math.exp(-xg), 4)
                r["cuota_justa_gol"] = round(1 / max(r["prob_gol"], 1e-6), 2)
                goleadores.append({"lado": lado, "n": r["n"], "prob": r["prob_gol"], "xg": round(xg, 3),
                                   "historia": mins >= 270})
            filas.append(r)
            lineas[ln]["ataque"] += J.att(j)
            lineas[ln]["defensa"] += J.dfn(j)
            lineas[ln]["n"] += 1
        orden = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}
        filas.sort(key=lambda r: (orden[r["linea"]], str(r.get("pos"))))
        once, hab = set(lados[lado]["once"]), set(lados[lado]["hab"])
        st = estilos_eq.get(eq["id"])
        grp_rival = (grupos.get(e[rival]["id"]) or {}).get("grupo")
        salida[lado] = {
            "formacion": lados[lado]["formacion"], "jugadores": filas,
            "sale": [J.info.get(j, {}).get("n") for j in hab - once] if confirmada else [],
            "entra": [J.info.get(j, {}).get("n") for j in once - hab] if confirmada else [],
            "delta_ataque": round(d_att, 3), "delta_defensa": round(d_def, 3),
            "lineas": {k: {"ataque": round(v["ataque"], 3), "defensa": round(v["defensa"], 3), "n": v["n"]}
                       for k, v in lineas.items()},
            "estilo": {k: round(v, 3) for k, v in st.items()} if st else None,
            "grupo_estilo": grupos.get(eq["id"]),
            "vs_similares": contra_similares(idx, grupos, eq["id"], grp_rival) if grp_rival is not None else None,
            "estilo_rival": (grupos.get(e[rival]["id"]) or {}).get("nombre"),
        }
    # enfrentamientos previos entre ambos, con cuánto se parecía cada once al de hoy
    previos = []
    for x, _ in idx.get(e["h"]["id"], [])[::-1]:
        ids = {x["r"]["equipos"]["h"]["id"], x["r"]["equipos"]["a"]["id"]}
        if ids != {e["h"]["id"], e["a"]["id"]}:
            continue
        fila = {"fecha": x["e"]["fecha"], "lg": x["e"]["lg"], "local": x["e"]["h"]["n"], "visita": x["e"]["a"]["n"],
                "gl": x["gh"], "gv": x["ga"]}
        for lado in ("h", "a"):
            eq_id = e[lado]["id"]
            ladox = "h" if x["r"]["equipos"]["h"]["id"] == eq_id else "a"
            tit = {p["id"] for p in x["r"]["equipos"][ladox]["jugadores"] if p.get("tit")}
            fila["coincide_" + lado] = len(tit & set(lados[lado]["once"]))
        previos.append(fila)
        if len(previos) >= 5:
            break
    salida["previos"] = previos
    M = matriz(np.array([lh]), np.array([la]), rho)[0]
    salida["matriz"] = [[round(float(v), 6) for v in fila] for fila in M]
    goleadores.sort(key=lambda g: -g["prob"])
    salida["goleadores"] = goleadores
    return salida, fh, fa


# ---------------------------------------------------------------- estilo de juego

def _vector_estilo(st):
    pases = st.get("pases") or 0
    return {
        "posesion": st.get("posesion"), "pases": pases,
        "precision": (st.get("pases_ok") or 0) / pases if pases else None,
        "remates": st.get("remates"), "centros": st.get("centros"),
        "directo": (st.get("largos") or 0) / pases if pases else None,
        "presion": (st.get("entradas") or 0) + (st.get("intercepciones") or 0),
        "despejes": st.get("despejes"),
    }


def estilos(regs, n=10):
    """Estilo promedio de cada equipo en sus últimos n partidos con estadísticas."""
    por_equipo = defaultdict(list)
    for x in regs:
        for lado in ("h", "a"):
            eq = x["r"]["equipos"][lado]
            v = _vector_estilo(eq.get("estilo") or {})
            if all(v[k] is not None for k in VARIABLES_ESTILO):
                por_equipo[eq["id"]].append(v)
    out = {}
    for t, vs in por_equipo.items():
        vs = vs[-n:]
        if len(vs) >= 4:
            out[t] = {k: float(np.mean([v[k] for v in vs])) for k in VARIABLES_ESTILO}
            out[t]["n"] = len(vs)
    return out


def agrupar(estilos_eq, k=4, iters=30):
    """k-medias sobre los estilos estandarizados; cada grupo se nombra por su rasgo más marcado."""
    ids = sorted(estilos_eq)
    if len(ids) < 20:
        return {}, {}
    X = np.array([[estilos_eq[t][v] for v in VARIABLES_ESTILO] for t in ids])
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    # inicio determinista: los equipos más extremos en posesión, directo, centros y despejes
    semillas = [int(np.argmax(Z[:, VARIABLES_ESTILO.index(v)])) for v in ("posesion", "directo", "centros", "despejes")]
    C = Z[semillas[:k]].copy()
    for _ in range(iters):
        g = np.argmin(((Z[:, None, :] - C[None, :, :]) ** 2).sum(axis=2), axis=1)
        for c in range(k):
            if (g == c).any():
                C[c] = Z[g == c].mean(axis=0)
    nombres = {}
    usados = set()
    for c in range(k):
        orden = np.argsort(-C[c])
        for i in orden:
            nombre = NOMBRE_ESTILO[VARIABLES_ESTILO[i]]
            if nombre not in usados:
                usados.add(nombre)
                nombres[c] = nombre
                break
    grupo = {t: int(g[i]) for i, t in enumerate(ids)}
    z = {t: {v: round(float(Z[i, j]), 2) for j, v in enumerate(VARIABLES_ESTILO)} for i, t in enumerate(ids)}
    return {t: {"grupo": grupo[t], "nombre": nombres.get(grupo[t], f"Estilo {grupo[t] + 1}"), "z": z[t]}
            for t in ids}, nombres


def indice(regs):
    """{equipo: [(partido, lado)]} para no recorrer todos los partidos en cada consulta."""
    out = defaultdict(list)
    for x in regs:
        for lado in ("h", "a"):
            out[x["r"]["equipos"][lado]["id"]].append((x, lado))
    return out


def contra_similares(idx, grupos, equipo, grupo_rival, desde_dias=540):
    """Goles a favor y en contra frente a lo esperado contra rivales del mismo estilo que el de hoy."""
    limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=desde_dias)
    gf = ga = lf = la = 0.0
    n = 0
    g = e = p = 0
    for x, lado in idx.get(equipo, []):
        if x["lam"] is None or _fecha(x["e"]["fecha"]) < limite:
            continue
        for otro in ("a" if lado == "h" else "h",):
            rival = x["r"]["equipos"][otro]["id"]
            if (grupos.get(rival) or {}).get("grupo") != grupo_rival:
                continue
            mf, mc = (x["gh"], x["ga"]) if lado == "h" else (x["ga"], x["gh"])
            ef, ec = (x["lam"][0], x["lam"][1]) if lado == "h" else (x["lam"][1], x["lam"][0])
            gf += mf
            ga += mc
            lf += ef
            la += ec
            n += 1
            g += mf > mc
            e += mf == mc
            p += mf < mc
    if n == 0:
        return None
    return {"n": n, "g": g, "e": e, "p": p, "gf": round(gf / n, 2), "gc": round(ga / n, 2),
            "gf_esperado": round(lf / n, 2), "gc_esperado": round(la / n, 2),
            # diferencia contraída hacia 0 (con pocos partidos no dice casi nada)
            "dif_ataque": round((gf - lf) / (n + 5), 3), "dif_defensa": round((ga - la) / (n + 5), 3)}
