"""Mercados de córners, tarjetas (con árbitro donde hay dato) y primer/segundo tiempo.

Mismo enfoque que el modelo de goles: fuerzas por equipo con ponderación temporal y regresión a la media
(prior Gamma), localía por competición y una distribución con sobredispersión medida en los datos (binomial
negativa). Cada mercado se valida fuera de muestra y solo se le da "cuota mínima" si pasa la puerta de
calibración (los que no la pasan se muestran como información).
"""
import datetime as dt
import math

import numpy as np

from .datos import marcador_90

MAXC = 31                      # conteos de 0 a 30
LINEAS_CORNERS = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
LINEAS_CORNERS_EQ = [2.5, 3.5, 4.5, 5.5, 6.5]
LINEAS_TARJETAS = [2.5, 3.5, 4.5, 5.5, 6.5]
LINEAS_TARJETAS_EQ = [0.5, 1.5, 2.5, 3.5]
ECE_MAX, SESGO_MAX, N_MIN = 0.03, 0.015, 500


def _dias(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() / 86400.0


def _pmf_nb(m, phi):
    """Distribución de un conteo con media m y dispersión phi = var/media (Poisson si phi≈1)."""
    k = np.arange(MAXC)
    m = max(float(m), 1e-6)
    if phi <= 1.02:
        lp = k * math.log(m) - m - np.array([math.lgamma(i + 1) for i in k])
    else:
        r = m / (phi - 1)
        p = r / (r + m)
        lp = (np.array([math.lgamma(i + r) for i in k]) - math.lgamma(r) - np.array([math.lgamma(i + 1) for i in k])
              + r * math.log(p) + k * math.log(1 - p))
    pm = np.exp(lp)
    return pm / pm.sum()


class ModeloConteo:
    """Conteo por equipo (córners o tarjetas): Y_local ~ μ·H_comp·genera_local·concede_visita (y al revés)."""

    def __init__(self, eventos, campo, idx_equipo, t_ref, vida=200.0, k_eq=12.0, k_comp=60.0, sel_comp=None):
        filas = []
        for e in eventos:
            v = _valor(e, campo)
            if v is None or not e["fin"] or _dias(e["fecha"]) >= t_ref:
                continue
            if sel_comp is not None and e["lg"] not in sel_comp:
                continue
            filas.append((idx_equipo[e["h"]["id"]], idx_equipo[e["a"]["id"]], e["lg"], v[0], v[1], _dias(e["fecha"])))
        self.ok = len(filas) >= 300
        self.n = len(filas)
        if not self.ok:
            return
        comps = sorted({f[2] for f in filas})
        self.cidx = {c: i for i, c in enumerate(comps)}
        h = np.array([f[0] for f in filas])
        a = np.array([f[1] for f in filas])
        c = np.array([self.cidx[f[2]] for f in filas])
        yh = np.array([f[3] for f in filas], float)
        ya = np.array([f[4] for f in filas], float)
        w = np.exp(-math.log(2) * (t_ref - np.array([f[5] for f in filas])) / vida)
        nT, nC = len(idx_equipo), len(comps)
        self.gen = np.ones(nT)
        self.con = np.ones(nT)
        self.Hc = np.ones(nC)
        self.Ac = np.ones(nC)
        for _ in range(40):
            mh = self.Hc[c] * self.gen[h] * self.con[a]
            ma = self.Ac[c] * self.gen[a] * self.con[h]
            for arr, y, mm in ((self.Hc, yh, mh), (self.Ac, ya, ma)):
                num = np.bincount(c, w * y, nC)
                den = np.bincount(c, w * mm / arr[c], nC)
                glob = num.sum() / max(den.sum(), 1e-9)
                arr[:] = (num + k_comp * glob) / (den + k_comp)
            mh = self.Hc[c] * self.gen[h] * self.con[a]
            ma = self.Ac[c] * self.gen[a] * self.con[h]
            G = np.bincount(h, w * yh, nT) + np.bincount(a, w * ya, nT)
            E = np.bincount(h, w * mh / self.gen[h], nT) + np.bincount(a, w * ma / self.gen[a], nT)
            self.gen = (G + k_eq) / (E + k_eq)
            mh = self.Hc[c] * self.gen[h] * self.con[a]
            ma = self.Ac[c] * self.gen[a] * self.con[h]
            G = np.bincount(a, w * yh, nT) + np.bincount(h, w * ya, nT)
            E = np.bincount(a, w * mh / self.con[a], nT) + np.bincount(h, w * ma / self.con[h], nT)
            self.con = (G + k_eq) / (E + k_eq)
        mh = self.Hc[c] * self.gen[h] * self.con[a]
        ma = self.Ac[c] * self.gen[a] * self.con[h]
        # sobredispersión por competición (Pearson), contraída hacia la global
        r2 = ((yh - mh) ** 2 / mh + (ya - ma) ** 2 / ma) / 2
        glob = float(np.average(r2, weights=w))
        self.phi = np.array([(np.sum(w[c == i] * r2[c == i]) + 30 * glob) / (np.sum(w[c == i]) + 30)
                             for i in range(nC)])
        self.phi_glob = glob
        self.media_comp = {comp: float(np.average(yh[c == i] + ya[c == i], weights=w[c == i]))
                           for comp, i in self.cidx.items() if (c == i).sum() >= 30}

    def medias(self, e, idx_equipo):
        ci = self.cidx.get(e["lg"])
        H = self.Hc[ci] if ci is not None else float(np.exp(np.log(self.Hc).mean()))
        A = self.Ac[ci] if ci is not None else float(np.exp(np.log(self.Ac).mean()))
        hi, ai = idx_equipo[e["h"]["id"]], idx_equipo[e["a"]["id"]]
        phi = self.phi[ci] if ci is not None else self.phi_glob
        return H * self.gen[hi] * self.con[ai], A * self.gen[ai] * self.con[hi], max(1.0, phi)


def _valor(e, campo):
    if campo == "cor":
        return e.get("cor")
    if campo == "tarj":
        if not e.get("con_detalle"):
            return None
        t = e["tarj"]
        return [t["h"][0] + t["h"][1], t["a"][0] + t["a"][1]]
    return None


def mercados_conteo(mh, ma, phi, lineas_total, lineas_eq, nombre, L, V):
    """Probabilidades de los mercados de un conteo a partir de las medias por equipo."""
    ph, pa = _pmf_nb(mh, phi), _pmf_nb(ma, phi)
    tot = np.convolve(ph, pa)[:2 * MAXC]
    k = np.arange(len(tot))
    out = []
    for x in lineas_total:
        p = float(tot[k > x].sum())
        out.append({"cod": f"{nombre}_O{x}", "txt": f"Más de {str(x).replace('.', ',')} {nombre_es(nombre)}", "p": p})
        out.append({"cod": f"{nombre}_U{x}", "txt": f"Menos de {str(x).replace('.', ',')} {nombre_es(nombre)}",
                    "p": 1 - p})
    kk = np.arange(MAXC)
    for lado, pm, eq in (("L", ph, L), ("V", pa, V)):
        for x in lineas_eq:
            p = float(pm[kk > x].sum())
            out.append({"cod": f"{nombre}_{lado}_O{x}", "txt": f"{eq}: más de {str(x).replace('.', ',')} "
                                                               f"{nombre_es(nombre)}", "p": p})
    M = np.outer(ph, pa)
    out.append({"cod": f"{nombre}_1", "txt": f"Más {nombre_es(nombre)}: {L}", "p": float(np.tril(M, -1).sum())})
    out.append({"cod": f"{nombre}_X", "txt": f"Mismos {nombre_es(nombre)}", "p": float(np.trace(M))})
    out.append({"cod": f"{nombre}_2", "txt": f"Más {nombre_es(nombre)}: {V}", "p": float(np.triu(M, 1).sum())})
    return out, float(mh + ma)


def nombre_es(n):
    return {"cor": "córners", "tarj": "tarjetas"}[n]


def liquidar_conteo(cod, vh, va):
    """True/False: si la opción de córners o tarjetas ganó con los conteos reales."""
    partes = cod.split("_")
    if partes[1] in ("1", "X", "2"):
        return {"1": vh > va, "X": vh == va, "2": vh < va}[partes[1]]
    if partes[1] in ("L", "V"):
        x = float(partes[2][1:])
        return (vh if partes[1] == "L" else va) > x
    x = float(partes[1][1:])
    return (vh + va > x) if partes[1][0] == "O" else (vh + va < x)


# ---------------------------------------------------------------- primer tiempo

def proporcion_1t(eventos, t_ref, k=300.0):
    """Parte de los goles que cae en el primer tiempo, por competición (contraída hacia la global)."""
    g1 = {}
    for e in eventos:
        if not e["fin"] or not e.get("g1t") or _dias(e["fecha"]) >= t_ref:
            continue
        m90 = marcador_90(e)
        if m90 is None:
            continue
        x = g1.setdefault(e["lg"], [0, 0])
        x[0] += e["g1t"][0] + e["g1t"][1]
        x[1] += m90[0] + m90[1]
    tot1, tot = sum(v[0] for v in g1.values()), sum(v[1] for v in g1.values())
    glob = tot1 / tot if tot else 0.44
    return {lg: (v[0] + k * glob) / (v[1] + k) for lg, v in g1.items()}, glob


def mercados_tiempos(lh, la, s, L, V):
    """Primer tiempo con λ·s y segundo tiempo con λ·(1−s) (Poisson independiente)."""
    i = np.arange(11)

    def pmf(l):
        return np.exp(i * math.log(max(l, 1e-6)) - l - np.array([math.lgamma(x + 1) for x in i]))

    out = []
    for parte, f, et in (("1T", s, "1.er tiempo"), ("2T", 1 - s, "2.º tiempo")):
        ph, pa = pmf(lh * f), pmf(la * f)
        M = np.outer(ph, pa)
        M /= M.sum()
        tot = i[:, None] + i[None, :]
        for x in (0.5, 1.5, 2.5):
            p = float(M[tot > x].sum())
            out.append({"cod": f"{parte}_O{x}", "txt": f"{et}: más de {str(x).replace('.', ',')} goles", "p": p})
            out.append({"cod": f"{parte}_U{x}", "txt": f"{et}: menos de {str(x).replace('.', ',')} goles", "p": 1 - p})
        if parte == "1T":
            out.append({"cod": "1T_1", "txt": f"Gana {L} al descanso", "p": float(np.tril(M, -1).sum())})
            out.append({"cod": "1T_X", "txt": "Empate al descanso", "p": float(np.trace(M))})
            out.append({"cod": "1T_2", "txt": f"Gana {V} al descanso", "p": float(np.triu(M, 1).sum())})
            out.append({"cod": "1T_BTTS", "txt": "Ambos marcan en el 1.er tiempo", "p": float(M[1:, 1:].sum())})
    # --- combinados de los dos tiempos (1.er y 2.º tiempo independientes)
    M1 = np.outer(pmf(lh * s), pmf(la * s))
    M2 = np.outer(pmf(lh * (1 - s)), pmf(la * (1 - s)))
    M1, M2 = M1 / M1.sum(), M2 / M2.sum()
    d = i[:, None] - i[None, :]

    def dist_dif(M):                      # P(diferencia = k) para k de -10 a 10
        return np.array([M[d == k].sum() for k in range(-10, 11)])

    d1, d2 = dist_dif(M1), dist_dif(M2)
    ks = np.arange(-10, 11)
    nombre_r = {"1": f"gana {L}", "X": "empate", "2": f"gana {V}"}
    signo = {"1": ks > 0, "X": ks == 0, "2": ks < 0}
    kft = np.arange(-20, 21)                          # diferencia final = dif. 1.er tiempo + dif. 2.º tiempo
    for r1 in "1X2":
        dft = np.convolve(d1 * signo[r1], d2)
        for r2 in "1X2":
            sel_ft = {"1": kft > 0, "X": kft == 0, "2": kft < 0}[r2]
            out.append({"cod": f"TT_HF{r1}{r2}", "txt": f"Descanso/final: {nombre_r[r1]} / {nombre_r[r2]}",
                        "p": float(dft[sel_ft].sum())})
    t1 = M1.sum(axis=None)
    p1 = 1 - M1[0, 0] / t1
    p2 = 1 - M2[0, 0] / M2.sum()
    out.append({"cod": "TT_AMBOS", "txt": "Hay gol en los dos tiempos", "p": float(p1 * p2)})
    out.append({"cod": "TT_L_AMBOS", "txt": f"{L} marca en los dos tiempos",
                "p": float((1 - M1[0, :].sum()) * (1 - M2[0, :].sum()))})
    out.append({"cod": "TT_V_AMBOS", "txt": f"{V} marca en los dos tiempos",
                "p": float((1 - M1[:, 0].sum()) * (1 - M2[:, 0].sum()))})
    tot1 = np.array([M1[(i[:, None] + i[None, :]) == k].sum() for k in range(21)])
    tot2 = np.array([M2[(i[:, None] + i[None, :]) == k].sum() for k in range(21)])
    T = np.outer(tot1, tot2)
    out.append({"cod": "TT_MAS1", "txt": "Más goles en el 1.er tiempo", "p": float(np.tril(T, -1).sum())})
    out.append({"cod": "TT_IGUAL", "txt": "Mismos goles en los dos tiempos", "p": float(np.trace(T))})
    out.append({"cod": "TT_MAS2", "txt": "Más goles en el 2.º tiempo", "p": float(np.triu(T, 1).sum())})
    return out


def liquidar_tiempos(cod, g1, g90):
    """g1 = goles al descanso [l, v]; g90 = marcador a 90'."""
    parte, sel = cod.split("_", 1)
    if parte == "TT":                                   # combinados de los dos tiempos
        h2, a2 = g90[0] - g1[0], g90[1] - g1[1]
        res = lambda a, b: "1" if a > b else "X" if a == b else "2"
        if sel.startswith("HF"):
            return res(g1[0], g1[1]) == sel[2] and res(g90[0], g90[1]) == sel[3]
        if sel == "AMBOS":
            return sum(g1) > 0 and h2 + a2 > 0
        if sel == "L_AMBOS":
            return g1[0] > 0 and h2 > 0
        if sel == "V_AMBOS":
            return g1[1] > 0 and a2 > 0
        t1, t2 = sum(g1), h2 + a2
        return {"MAS1": t1 > t2, "IGUAL": t1 == t2, "MAS2": t1 < t2}[sel]
    gl, gv = (g1[0], g1[1]) if parte == "1T" else (g90[0] - g1[0], g90[1] - g1[1])
    if sel in ("1", "X", "2"):
        return {"1": gl > gv, "X": gl == gv, "2": gl < gv}[sel]
    if sel == "BTTS":
        return gl > 0 and gv > 0
    x = float(sel[1:])
    return (gl + gv > x) if sel[0] == "O" else (gl + gv < x)


# ---------------------------------------------------------------- validación (fuera de muestra)

def _ece(ps, ys):
    ps, ys = np.array(ps), np.array(ys, float)
    e = 0.0
    for lo in np.arange(0, 1, 0.1):
        m = (ps >= lo) & (ps < lo + 0.1)
        if m.any():
            e += m.mean() * abs(ps[m].mean() - ys[m].mean())
    return float(e)


def resumen_calibracion(pred):
    """pred = {cod: [(p, gano)]} → {cod: {n, esperado, real, ece, sesgo, ok}} con la misma puerta que las apuestas."""
    out = {}
    for cod, xs in pred.items():
        if len(xs) < 50:
            continue
        ps = [x[0] for x in xs]
        ys = [x[1] for x in xs]
        esp, real = float(np.mean(ps)), float(np.mean(ys))
        ece = _ece(ps, ys)
        out[cod] = {"n": len(xs), "esperado": round(esp, 4), "real": round(real, 4), "ece": round(ece, 4),
                    "sesgo": round(real - esp, 4),
                    "ok": len(xs) >= N_MIN and ece <= ECE_MAX and real - esp >= -SESGO_MAX}
    return out


def validar_conteos(eventos, idx_equipo, ahora, campo, sel_comp=None, cortes=6, semanas=4, vida=200.0):
    """Ajusta con lo anterior a cada corte y predice las semanas siguientes (sin mirar el futuro)."""
    lt, le = (LINEAS_CORNERS, LINEAS_CORNERS_EQ) if campo == "cor" else (LINEAS_TARJETAS, LINEAS_TARJETAS_EQ)
    casos = []                     # (corte, mh, ma, phi, real)
    for k in range(cortes, 0, -1):
        t0 = ahora - 7 * semanas * k
        t1 = t0 + 7 * semanas
        mod = ModeloConteo(eventos, campo, idx_equipo, t0, vida, sel_comp=sel_comp)
        if not mod.ok:
            continue
        for e in eventos:
            v = _valor(e, campo)
            if v is None or not e["fin"] or not (t0 <= _dias(e["fecha"]) < t1):
                continue
            if sel_comp is not None and e["lg"] not in sel_comp:
                continue
            mh, ma, phi = mod.medias(e, idx_equipo)
            casos.append((k, mh, ma, phi, v))
    if not casos:
        return {}, 1.0
    # factor de nivel: se aprende con los cortes antiguos y se verifica con los recientes (fuera de muestra)
    mitad = cortes // 2
    viejos = [c for c in casos if c[0] > mitad]
    factor = sum(c[4][0] + c[4][1] for c in viejos) / max(sum(c[1] + c[2] for c in viejos), 1e-9) if viejos else 1.0
    factor = float(min(max(factor, 0.8), 1.25))
    pred = {}
    for k, mh, ma, phi, v in casos:
        if k > mitad:
            continue
        filas, _ = mercados_conteo(mh * factor, ma * factor, phi, lt, le, campo, "L", "V")
        for f in filas:
            pred.setdefault(f["cod"], []).append((f["p"], liquidar_conteo(f["cod"], v[0], v[1])))
    return resumen_calibracion(pred), round(factor, 4)


def validar_tiempos(eventos_por_id, filas_val, ahora):
    """Usa los goles esperados fuera de muestra de la prueba retrospectiva del modelo principal."""
    if not filas_val:
        return {}
    filas = []
    for f in sorted(filas_val, key=lambda f: eventos_por_id[f[9]["id"]]["fecha"]):
        e = eventos_por_id[f[9]["id"]]
        m90 = marcador_90(e)
        if e.get("g1t") and m90 is not None:
            filas.append((f, e, m90))
    if not filas:
        return {}
    pred = {}
    # proporción del 1.er tiempo aprendida solo con partidos anteriores al primero de la prueba
    t0 = _dias(filas[0][1]["fecha"])
    props, glob = proporcion_1t(eventos_por_id.values(), t0)
    for f, e, m90 in filas:
        s = props.get(e["lg"], glob)
        for x in mercados_tiempos(f[9]["lh"], f[9]["la"], s, "L", "V"):
            pred.setdefault(x["cod"], []).append((x["p"], liquidar_tiempos(x["cod"], e["g1t"], m90)))
    return resumen_calibracion(pred)


# ---------------------------------------------------------------- árbitros (Inglaterra y Escocia)

def efecto_arbitro(registros, k=30.0):
    """registros: [(árbitro, liga, tarjetas_partido)]. Factor del árbitro frente a la media de su liga,
    contraído hacia 1 con k tarjetas de prior (con pocos partidos no dice casi nada: R15)."""
    por_liga = {}
    for _, lg, t in registros:
        x = por_liga.setdefault(lg, [0, 0])
        x[0] += t
        x[1] += 1
    media = {lg: v[0] / v[1] for lg, v in por_liga.items() if v[1]}
    arb = {}
    for r, lg, t in registros:
        if lg not in media:
            continue
        x = arb.setdefault(r, {"tarjetas": 0.0, "esperadas": 0.0, "n": 0})
        x["tarjetas"] += t
        x["esperadas"] += media[lg]
        x["n"] += 1
    for r, x in arb.items():
        x["factor"] = round((x["tarjetas"] + k) / (x["esperadas"] + k), 3)
        x["por_partido"] = round(x["tarjetas"] / x["n"], 2)
        x["media_liga"] = round(x["esperadas"] / x["n"], 2)
    return arb
