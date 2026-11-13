"""Modelo D24 del Prompt Maestro: probabilidades calibradas, calculadas con código (R17-R18).

lambda_L = mu * H_comp * h_estadio * A_L * D_V * prod(f)       (goles esperados del local)
lambda_V = mu * V_comp * v_estadio * A_V * D_L * prod(g)       (goles esperados del visitante)

- A (ataque) y D (debilidad defensiva): Poisson ponderado por antigüedad (D24.3) con regresión a
  la media de la liga del equipo (prior Gamma) para muestras pequeñas o equipos nuevos (D24.11).
- H/V: localía de cada competición; h/v: localía propia de cada estadio, muy regularizada (D24.4).
- f/g: ajustes acotados estimados con los propios datos (R19): altitud (D18.1), descanso corto
  (D24.8) y riesgo de rotación por copa continental cercana (D24.6).
- Corrección Dixon-Coles para marcadores bajos (D24.13) con rho estimado.
- Intervalos: simulación de la incertidumbre de A y D (R17).
"""
import datetime as dt
import math

import numpy as np

from .config import (COPAS_PRIORITARIAS, INFO_LIGA, PRIOR_EQUIPO, PRIOR_LOCALIA_COMP,
                     PRIOR_LOCALIA_EQUIPO, SIMULACIONES_INTERVALO)
from .datos import marcador_90

ESTADOS_INVALIDOS = ("ABANDON", "CANCEL", "POSTPON", "FORFEIT", "SUSPEND", "DELAY")
MAXG = 11
_LGF = np.array([math.lgamma(i + 1) for i in range(MAXG)])

COEF = {  # nombre: (desviación del prior, límite)
    "alt_local": (0.30, 0.60), "alt_visita": (0.30, 0.60),
    "descanso_ataque": (0.08, 0.20), "descanso_defensa": (0.08, 0.20),
    "rotacion_ataque": (0.08, 0.20), "rotacion_defensa": (0.08, 0.20),
}
NOMBRE_COEF = {
    "alt_local": "Altitud: goles del local por cada 1000 m de diferencia",
    "alt_visita": "Altitud: goles del visitante por cada 1000 m de diferencia",
    "descanso_ataque": "Descanso de 3 días o menos: goles a favor",
    "descanso_defensa": "Descanso de 3 días o menos: goles en contra",
    "rotacion_ataque": "Copa continental en 4 días o menos: goles a favor",
    "rotacion_defensa": "Copa continental en 4 días o menos: goles en contra",
}


def _dias(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() / 86400.0


def _valido(e):
    s = (e.get("status") or "").upper()
    return not any(x in s for x in ESTADOS_INVALIDOS)


class Datos:
    """Prepara índices y covariables a partir de los partidos compactados."""

    def __init__(self, eventos, lugares, clave_lugar):
        self.eventos = [e for e in eventos if e.get("fecha") and _valido(e)]
        self.eventos.sort(key=lambda e: e["fecha"])
        self.ahora = dt.datetime.now(dt.timezone.utc).timestamp() / 86400.0
        # --- equipos
        self.idx, self.equipos = {}, []
        for e in self.eventos:
            for lado in ("h", "a"):
                t = e[lado]
                if t["id"] not in self.idx:
                    self.idx[t["id"]] = len(self.equipos)
                    self.equipos.append({"id": t["id"], "n": t["n"], "c": t["c"], "logo": t["logo"]})
                else:
                    q = self.equipos[self.idx[t["id"]]]
                    q["n"], q["logo"] = t["n"] or q["n"], t["logo"] or q["logo"]
        self.nT = len(self.equipos)
        self.comps = sorted({e["lg"] for e in self.eventos})
        self.cidx = {c: i for i, c in enumerate(self.comps)}
        self.nC = len(self.comps)

        # --- calendario por equipo (descanso y rotación)
        cal = [[] for _ in range(self.nT)]
        for e in self.eventos:
            t = _dias(e["fecha"])
            for lado in ("h", "a"):
                cal[self.idx[e[lado]["id"]]].append((t, e["lg"], e["id"]))
        self.calendario = cal
        self.descanso, self.rotacion = {}, {}
        for i, lista in enumerate(cal):
            for k, (t, lg, eid) in enumerate(lista):
                prev = lista[k - 1][0] if k > 0 else None
                self.descanso[(eid, i)] = (t - prev) if prev is not None else None
                rot = False
                if INFO_LIGA.get(lg, {}).get("tipo") == "liga":
                    for t2, lg2, _ in lista[k + 1:k + 4]:
                        if t2 - t > 4.5:
                            break
                        if lg2 in COPAS_PRIORITARIAS:
                            rot = lg2
                            break
                self.rotacion[(eid, i)] = rot

        # --- grupo de cada equipo (liga principal) para la regresión a la media
        cuenta = [dict() for _ in range(self.nT)]
        for e in self.eventos:
            if self.ahora - _dias(e["fecha"]) > 400:
                continue
            for lado in ("h", "a"):
                d = cuenta[self.idx[e[lado]["id"]]]
                d[e["lg"]] = d.get(e["lg"], 0) + 1
        grupos = []
        for i in range(self.nT):
            d = cuenta[i]
            ligas = {k: v for k, v in d.items() if INFO_LIGA.get(k, {}).get("tipo") == "liga"}
            if ligas:
                grupos.append(max(ligas, key=ligas.get))
            elif d:
                grupos.append("orf:" + max(d, key=d.get))
            else:
                grupos.append("orf:?")
        self.gnombres = sorted(set(grupos))
        gi = {g: k for k, g in enumerate(self.gnombres)}
        self.grupo = np.array([gi[g] for g in grupos])
        self.grupo_nombre = grupos

        # --- ciudad y altitud de cada equipo
        ciudades = [dict() for _ in range(self.nT)]
        for e in self.eventos:
            if e.get("neutral") or not e.get("ciudad"):
                continue
            k = clave_lugar(e["ciudad"], e["pais"])
            d = ciudades[self.idx[e["h"]["id"]]]
            d[k] = d.get(k, 0) + 1
        self.alt_equipo = np.full(self.nT, np.nan)
        self.ciudad_equipo = [None] * self.nT
        for i, d in enumerate(ciudades):
            if d:
                k = max(d, key=d.get)
                self.ciudad_equipo[i] = k
                lu = lugares.get(k)
                if lu and lu.get("alt") is not None:
                    self.alt_equipo[i] = lu["alt"]
        self.lugares, self.clave_lugar = lugares, clave_lugar

        # --- matrices de partidos jugados (con el marcador a los 90', que es lo que predice el modelo)
        jug = [e for e in self.eventos if e["fin"] and marcador_90(e) is not None]
        self.jugados = jug
        n = len(jug)
        m90 = [marcador_90(e) for e in jug]
        self.t = np.array([_dias(e["fecha"]) for e in jug])
        self.h = np.array([self.idx[e["h"]["id"]] for e in jug], dtype=int)
        self.a = np.array([self.idx[e["a"]["id"]] for e in jug], dtype=int)
        self.yh = np.array([g[0] for g in m90], dtype=float)
        self.ya = np.array([g[1] for g in m90], dtype=float)
        self.c = np.array([self.cidx[e["lg"]] for e in jug], dtype=int)
        self.neu = np.array([bool(e.get("neutral")) for e in jug])
        X = [self.covariables(e) for e in jug]
        self.X = {k: np.array([x[k] for x in X], dtype=float) for k in ("dalt", "Sh", "Sa", "Rh", "Ra")} if n else \
            {k: np.zeros(0) for k in ("dalt", "Sh", "Sa", "Rh", "Ra")}
        # tarjetas y penales (solo competiciones con cobertura de detalle)
        self.det = np.array([bool(e.get("con_detalle")) for e in jug])
        self.amh = np.array([e["tarj"]["h"][0] for e in jug], dtype=float)
        self.ama = np.array([e["tarj"]["a"][0] for e in jug], dtype=float)
        self.roh = np.array([e["tarj"]["h"][1] for e in jug], dtype=float)
        self.roa = np.array([e["tarj"]["a"][1] for e in jug], dtype=float)
        self.peh = np.array([e["pen"]["h"] for e in jug], dtype=float)
        self.pea = np.array([e["pen"]["a"] for e in jug], dtype=float)
        cobertura = {}
        for e in jug:
            if self.ahora - _dias(e["fecha"]) < 365:
                c = cobertura.setdefault(e["lg"], [0, 0])
                c[0] += 1
                c[1] += bool(e.get("con_detalle"))
        self.cobertura = {k: v[1] / v[0] for k, v in cobertura.items() if v[0]}
        cob = np.array([self.cobertura.get(c, 0) >= 0.7 for c in self.comps])
        self.det = self.det & (cob[self.c] if n else self.det)

    def alt_lugar(self, e):
        lu = self.lugares.get(self.clave_lugar(e.get("ciudad"), e.get("pais")))
        return lu.get("alt") if lu else None

    def covariables(self, e):
        hi, ai = self.idx[e["h"]["id"]], self.idx[e["a"]["id"]]
        dalt = 0.0
        if not e.get("neutral"):
            av = self.alt_lugar(e)
            ah = self.alt_equipo[ai]
            if av is not None and not np.isnan(ah):
                dalt = max(0.0, (av - ah) / 1000.0)
        dh, da = self.descanso.get((e["id"], hi)), self.descanso.get((e["id"], ai))
        return {
            "dalt": dalt,
            "Sh": 1.0 if dh is not None and dh <= 3.5 else 0.0,
            "Sa": 1.0 if da is not None and da <= 3.5 else 0.0,
            "Rh": 1.0 if self.rotacion.get((e["id"], hi)) else 0.0,
            "Ra": 1.0 if self.rotacion.get((e["id"], ai)) else 0.0,
        }


class Parametros:
    def __init__(self, D):
        self.att = np.ones(D.nT)
        self.dfn = np.ones(D.nT)
        self.Hh = np.full(D.nC, 1.15)
        self.Ha = np.full(D.nC, 0.90)
        self.tha = np.ones(D.nT)
        self.thd = np.ones(D.nT)
        self.mu = 1.3
        self.b = {k: 0.0 for k in COEF}
        self.rho = -0.08
        self.var_att = np.full(D.nT, 1 / PRIOR_EQUIPO)
        self.var_dfn = np.full(D.nT, 1 / PRIOR_EQUIPO)
        self.matt = np.ones(D.nT)
        self.mdfn = np.ones(D.nT)
        self.expo = np.zeros(D.nT)

    def copia(self):
        import copy
        return copy.deepcopy(self)


def _lambdas(P, h, a, c, neu, X):
    g = np.sqrt(P.Hh[c] * P.Ha[c])
    fh = np.where(neu, g, P.Hh[c] * P.tha[h])
    fa = np.where(neu, g, P.Ha[c] * P.thd[h])
    b = P.b
    eh = b["alt_local"] * X["dalt"] + b["descanso_ataque"] * X["Sh"] + b["descanso_defensa"] * X["Sa"] + \
        b["rotacion_ataque"] * X["Rh"] + b["rotacion_defensa"] * X["Ra"]
    ea = b["alt_visita"] * X["dalt"] + b["descanso_ataque"] * X["Sa"] + b["descanso_defensa"] * X["Sh"] + \
        b["rotacion_ataque"] * X["Ra"] + b["rotacion_defensa"] * X["Rh"]
    lh = P.mu * fh * P.att[h] * P.dfn[a] * np.exp(eh)
    la = P.mu * fa * P.att[a] * P.dfn[h] * np.exp(ea)
    return lh, la


def _media_grupo(D, valores, expo):
    lg = np.log(np.maximum(valores, 1e-6))
    num = np.bincount(D.grupo, weights=expo * lg, minlength=len(D.gnombres))
    den = np.bincount(D.grupo, weights=expo, minlength=len(D.gnombres))
    m = np.where(den > 0, num / np.maximum(den, 1e-9), 0.0)
    return np.exp(m)[D.grupo]


def ajustar(D, sel, t_ref, vida, iters=120, inicial=None):
    """Ajusta el modelo con los partidos `sel` (máscara) ponderados hacia la fecha t_ref."""
    P = inicial.copia() if inicial is not None else Parametros(D)
    h, a, c, neu = D.h[sel], D.a[sel], D.c[sel], D.neu[sel]
    yh, ya = D.yh[sel], D.ya[sel]
    X = {k: v[sel] for k, v in D.X.items()}
    w = np.exp(-math.log(2) * np.maximum(t_ref - D.t[sel], 0) / vida)
    nT, nC, k = D.nT, D.nC, PRIOR_EQUIPO
    if len(w) == 0:
        return P
    expo = np.bincount(h, w, nT) + np.bincount(a, w, nT)
    P.expo = expo
    nn = ~neu
    for it in range(iters):
        viejo = np.log(P.att).copy()
        # ataque
        lh, la = _lambdas(P, h, a, c, neu, X)
        G = np.bincount(h, w * yh, nT) + np.bincount(a, w * ya, nT)
        E = np.bincount(h, w * lh / P.att[h], nT) + np.bincount(a, w * la / P.att[a], nT)
        m = _media_grupo(D, P.att, expo)
        P.att = (G + k * m) / (E + k)
        # varianza del logaritmo: acotada (con cientos de ligas hay grupos casi sin datos y la simulación explotaría)
        P.matt, P.var_att = m, np.minimum(1.0 / (P.att * E + k * m), VAR_MAX)
        # defensa
        lh, la = _lambdas(P, h, a, c, neu, X)
        C = np.bincount(a, w * yh, nT) + np.bincount(h, w * ya, nT)
        F = np.bincount(a, w * lh / P.dfn[a], nT) + np.bincount(h, w * la / P.dfn[h], nT)
        m = _media_grupo(D, P.dfn, expo)
        P.dfn = (C + k * m) / (F + k)
        P.mdfn, P.var_dfn = m, np.minimum(1.0 / (P.dfn * F + k * m), VAR_MAX)
        # localía por competición
        lh, la = _lambdas(P, h, a, c, neu, X)
        for arr, y, l in ((P.Hh, yh, lh), (P.Ha, ya, la)):
            num = np.bincount(c[nn], (w * y)[nn], nC)
            den = np.bincount(c[nn], (w * l)[nn] / arr[c[nn]], nC)
            glob = num.sum() / max(den.sum(), 1e-9)
            arr[:] = (num + PRIOR_LOCALIA_COMP * glob) / (den + PRIOR_LOCALIA_COMP)
        wc = np.bincount(c, w, nC)
        gc = math.exp((wc * 0.5 * (np.log(P.Hh) + np.log(P.Ha))).sum() / max(wc.sum(), 1e-9))
        P.Hh /= gc
        P.Ha /= gc
        P.mu *= gc
        # localía propia de cada estadio/equipo
        lh, la = _lambdas(P, h, a, c, neu, X)
        kt = PRIOR_LOCALIA_EQUIPO
        num = np.bincount(h[nn], (w * yh)[nn], nT)
        den = np.bincount(h[nn], (w * lh)[nn] / P.tha[h[nn]], nT)
        P.tha = (num + kt) / (den + kt)
        num = np.bincount(h[nn], (w * ya)[nn], nT)
        den = np.bincount(h[nn], (w * la)[nn] / P.thd[h[nn]], nT)
        P.thd = (num + kt) / (den + kt)
        eh_ = np.bincount(h[nn], w[nn], nT)
        if eh_.sum() > 0:
            ga = math.exp((eh_ * np.log(P.tha)).sum() / eh_.sum())
            gd = math.exp((eh_ * np.log(P.thd)).sum() / eh_.sum())
            P.tha /= ga
            P.Hh *= ga
            P.thd /= gd
            P.Ha *= gd
        # ajustes acotados (un paso de Newton con prior normal centrado en 0)
        lh, la = _lambdas(P, h, a, c, neu, X)
        cero = np.zeros_like(lh)
        diseno = {
            "alt_local": (X["dalt"], cero), "alt_visita": (cero, X["dalt"]),
            "descanso_ataque": (X["Sh"], X["Sa"]), "descanso_defensa": (X["Sa"], X["Sh"]),
            "rotacion_ataque": (X["Rh"], X["Ra"]), "rotacion_defensa": (X["Ra"], X["Rh"]),
        }
        for nombre, (xh, xa) in diseno.items():
            s, lim = COEF[nombre]
            b = P.b[nombre]
            grad = (w * (xh * (yh - lh) + xa * (ya - la))).sum() - b / s ** 2
            hess = (w * (xh ** 2 * lh + xa ** 2 * la)).sum() + 1 / s ** 2
            P.b[nombre] = float(np.clip(b + grad / hess, -lim, lim))
        # escala global y normalización
        lh, la = _lambdas(P, h, a, c, neu, X)
        P.mu *= (w * (yh + ya)).sum() / max((w * (lh + la)).sum(), 1e-9)
        act = expo > 0
        ga = math.exp((expo[act] * np.log(P.att[act])).sum() / expo[act].sum())
        gd = math.exp((expo[act] * np.log(P.dfn[act])).sum() / expo[act].sum())
        P.att /= ga
        P.dfn /= gd
        P.matt /= ga
        P.mdfn /= gd
        P.mu *= ga * gd
        if it > 5 and np.max(np.abs(np.log(P.att) - viejo)) < 2e-5:
            break
    # rho de Dixon-Coles por máxima verosimilitud en rejilla
    lh, la = _lambdas(P, h, a, c, neu, X)
    mejor, mejor_ll = P.rho, -1e18
    for rho in np.arange(-0.25, 0.101, 0.01):
        tau = np.ones_like(lh)
        m00 = (yh == 0) & (ya == 0)
        m01 = (yh == 0) & (ya == 1)
        m10 = (yh == 1) & (ya == 0)
        m11 = (yh == 1) & (ya == 1)
        tau[m00] = 1 - lh[m00] * la[m00] * rho
        tau[m01] = 1 + lh[m01] * rho
        tau[m10] = 1 + la[m10] * rho
        tau[m11] = 1 - rho
        if np.any(tau <= 0):
            continue
        ll = (w * np.log(tau)).sum()
        if ll > mejor_ll:
            mejor, mejor_ll = float(rho), ll
    P.rho = round(mejor, 3)
    return P


# ------------------------------------------------------------------ predicción

VAR_MAX = 0.5        # desviación del log ≤ 0,71: un equipo desconocido tiene incertidumbre grande, no infinita


def _pmf(l):
    l = np.atleast_1d(np.clip(np.nan_to_num(l, nan=1.0), 1e-6, 15.0))
    i = np.arange(MAXG)
    return np.exp(i[None, :] * np.log(l)[:, None] - l[:, None] - _LGF[None, :])


def matriz(lh, la, rho):
    """Matrices de marcadores (S x MAXG x MAXG) con corrección Dixon-Coles."""
    ph, pa = _pmf(lh), _pmf(la)
    M = ph[:, :, None] * pa[:, None, :]
    lh, la = np.atleast_1d(lh), np.atleast_1d(la)
    M[:, 0, 0] *= np.maximum(1 - lh * la * rho, 0)
    M[:, 0, 1] *= np.maximum(1 + lh * rho, 0)
    M[:, 1, 0] *= np.maximum(1 + la * rho, 0)
    M[:, 1, 1] *= max(1 - rho, 0)
    return M / M.sum(axis=(1, 2), keepdims=True)


_I = np.arange(MAXG)
_TOT = _I[:, None] + _I[None, :]


_LOC = (_I[:, None] > _I[None, :]).astype(float)
_VIS = (_I[:, None] < _I[None, :]).astype(float)
_EMP = np.eye(MAXG)


def resumen_matriz(M):
    """M[s, i, j]: probabilidad de que el local marque i y el visitante j."""
    pH = (M * _LOC).sum(axis=(1, 2))
    pA = (M * _VIS).sum(axis=(1, 2))
    pD = (M * _EMP).sum(axis=(1, 2))
    o = {f"o{x}": (M * (_TOT > x)).sum(axis=(1, 2)) for x in (1.5, 2.5, 3.5)}
    btts = M[:, 1:, 1:].sum(axis=(1, 2))
    return pH, pD, pA, o, btts


def lambdas_partido(P, D, e, cov=None):
    hi, ai = D.idx[e["h"]["id"]], D.idx[e["a"]["id"]]
    c = D.cidx.get(e["lg"])
    X = cov or D.covariables(e)
    Hh = P.Hh[c] if c is not None else float(np.exp(np.mean(np.log(P.Hh))))
    Ha = P.Ha[c] if c is not None else float(np.exp(np.mean(np.log(P.Ha))))
    if e.get("neutral"):
        fh = fa = math.sqrt(Hh * Ha)
        th, td = 1.0, 1.0
    else:
        th, td = P.tha[hi], P.thd[hi]
        fh, fa = Hh * th, Ha * td
    b = P.b
    eh = b["alt_local"] * X["dalt"] + b["descanso_ataque"] * X["Sh"] + b["descanso_defensa"] * X["Sa"] + \
        b["rotacion_ataque"] * X["Rh"] + b["rotacion_defensa"] * X["Ra"]
    ea = b["alt_visita"] * X["dalt"] + b["descanso_ataque"] * X["Sa"] + b["descanso_defensa"] * X["Sh"] + \
        b["rotacion_ataque"] * X["Ra"] + b["rotacion_defensa"] * X["Rh"]
    lh = P.mu * fh * P.att[hi] * P.dfn[ai] * math.exp(eh)
    la = P.mu * fa * P.att[ai] * P.dfn[hi] * math.exp(ea)
    mezcla = None
    if getattr(P, "conj", None) is not None:      # ligas de nivel 3-4: conjunto con memoria corta + Elo
        lh, la, mezcla = P.conj.ajustar(D, e, lh, la, P.rho, lambdas_partido, matriz, resumen_matriz)
    return {
        "hi": hi, "ai": ai, "c": c, "X": X, "Hh": Hh, "Ha": Ha, "th": th, "td": td, "eh": eh, "ea": ea,
        "lh": lh, "la": la, "mezcla": mezcla,
    }


def predecir(P, D, e, rng, sims=SIMULACIONES_INTERVALO, matrices=None, factores=None):
    """Predicción del partido. Si se pasa una lista en `matrices`, se le agregan (M, Ms) para las apuestas.
    `factores` = (local, visita): ajuste acotado por la alineación confirmada (D24.5, D24.16)."""
    L = lambdas_partido(P, D, e)
    if factores:
        L["lh"] *= factores[0]
        L["la"] *= factores[1]
        L["alineacion"] = factores
    hi, ai = L["hi"], L["ai"]
    M = matriz(np.array([L["lh"]]), np.array([L["la"]]), P.rho)[0]
    pH, pD, pA, o, btts = resumen_matriz(M[None])
    # incertidumbre de los parámetros (R17): se simulan A y D de ambos equipos
    z = rng.standard_normal((sims, 4))
    f = np.exp(z[:, 0] * math.sqrt(P.var_att[hi]) + z[:, 1] * math.sqrt(P.var_dfn[ai]))
    g = np.exp(z[:, 2] * math.sqrt(P.var_att[ai]) + z[:, 3] * math.sqrt(P.var_dfn[hi]))
    Ms = matriz(L["lh"] * f, L["la"] * g, P.rho)
    if matrices is not None:
        matrices.extend([M, Ms])
    sH, sD, sA, so, sb = resumen_matriz(Ms)

    def ic(x):
        return [round(float(np.percentile(x, 10)), 3), round(float(np.percentile(x, 90)), 3)]

    plano = [(int(i), int(j), float(M[i, j])) for i in range(MAXG) for j in range(MAXG)]
    plano.sort(key=lambda x: -x[2])
    ancho = max(ic(sH)[1] - ic(sH)[0], ic(sA)[1] - ic(sA)[0])
    exp_h, exp_a = D_expo(P, hi), D_expo(P, ai)
    if min(exp_h, exp_a) < 6 or ancho > 0.30:
        conf = "baja"
    elif min(exp_h, exp_a) >= 14 and ancho < 0.18:
        conf = "alta"
    else:
        conf = "media"
    totales = _I[:, None] + _I[None, :]
    dist_goles = [round(float(M[totales == n].sum()), 4) for n in range(7)]
    return {
        "pH": round(float(pH[0]), 4), "pD": round(float(pD[0]), 4), "pA": round(float(pA[0]), 4),
        "ic": {"pH": ic(sH), "pD": ic(sD), "pA": ic(sA), "o25": ic(so["o2.5"]), "btts": ic(sb)},
        "lh": round(L["lh"], 3), "la": round(L["la"], 3),
        "o15": round(float(o["o1.5"][0]), 4), "o25": round(float(o["o2.5"][0]), 4),
        "o35": round(float(o["o3.5"][0]), 4), "btts": round(float(btts[0]), 4),
        "top": [[i, j, round(p, 4)] for i, j, p in plano[:5]],
        "dist_goles": dist_goles, "conf": conf,
        "calc": _transparencia(P, D, L),
    }


def D_expo(P, i):
    """Partidos 'efectivos' del equipo en la muestra (peso temporal incluido)."""
    return float(P.expo[i]) if len(P.expo) > i else 0.0


def _transparencia(P, D, L):
    """Fórmula con valores de entrada para poder replicarla en Excel (R18)."""
    hi, ai, X = L["hi"], L["ai"], L["X"]
    fac = []
    b = P.b
    for nombre, xh, xa in (("alt_local", X["dalt"], 0), ("alt_visita", 0, X["dalt"]),
                           ("descanso_ataque", X["Sh"], X["Sa"]), ("descanso_defensa", X["Sa"], X["Sh"]),
                           ("rotacion_ataque", X["Rh"], X["Ra"]), ("rotacion_defensa", X["Ra"], X["Rh"])):
        if xh or xa:
            fac.append({"nombre": NOMBRE_COEF[nombre], "coef": round(b[nombre], 4),
                        "x_local": round(xh, 3), "x_visita": round(xa, 3),
                        "mult_local": round(math.exp(b[nombre] * xh), 4),
                        "mult_visita": round(math.exp(b[nombre] * xa), 4)})
    return {
        "mu": round(P.mu, 4), "H_comp": round(L["Hh"], 4), "V_comp": round(L["Ha"], 4),
        "h_estadio": round(L["th"], 4), "v_estadio": round(L["td"], 4),
        "A_local": round(float(P.att[hi]), 4), "D_local": round(float(P.dfn[hi]), 4),
        "A_visita": round(float(P.att[ai]), 4), "D_visita": round(float(P.dfn[ai]), 4),
        "media_liga_A_local": round(float(P.matt[hi]), 4), "media_liga_A_visita": round(float(P.matt[ai]), 4),
        "n_local": round(D_expo(P, hi), 1), "n_visita": round(D_expo(P, ai), 1),
        "rho": P.rho, "factores": fac, "dalt_km": round(X["dalt"], 3),
        "f_local": round(math.exp(L["eh"]) * (L.get("alineacion") or (1, 1))[0], 4),
        "f_visita": round(math.exp(L["ea"]) * (L.get("alineacion") or (1, 1))[1], 4),
        "alineacion": [round(x, 4) for x in L["alineacion"]] if L.get("alineacion") else None,
        "mezcla": L.get("mezcla"),
    }


# ------------------------------------------------------------------ mercados de apuestas

_GH, _GA = _I[:, None] + 0 * _I[None, :], 0 * _I[:, None] + _I[None, :]
_DIF, _SUMA = _GH - _GA, _GH + _GA


def _m(cond):
    return cond.astype(float)


_CERO = np.zeros((MAXG, MAXG))
# código: (texto con {L}/{V}, grupo, máscara "gana", máscara "devuelve")
MERCADOS = {
    "1": ("Gana {L}", "1X2", _m(_DIF > 0), _CERO),
    "X": ("Empate", "1X2", _m(_DIF == 0), _CERO),
    "2": ("Gana {V}", "1X2", _m(_DIF < 0), _CERO),
    "1X": ("{L} o empate (doble oportunidad)", "Doble oportunidad", _m(_DIF >= 0), _CERO),
    "X2": ("{V} o empate (doble oportunidad)", "Doble oportunidad", _m(_DIF <= 0), _CERO),
    "12": ("Gana cualquiera, sin empate", "Doble oportunidad", _m(_DIF != 0), _CERO),
    "DNB1": ("{L} gana (empate devuelve el dinero)", "Empate no válido", _m(_DIF > 0), _m(_DIF == 0)),
    "DNB2": ("{V} gana (empate devuelve el dinero)", "Empate no válido", _m(_DIF < 0), _m(_DIF == 0)),
    "O1.5": ("Más de 1,5 goles", "Goles", _m(_SUMA >= 2), _CERO),
    "O2.5": ("Más de 2,5 goles", "Goles", _m(_SUMA >= 3), _CERO),
    "O3.5": ("Más de 3,5 goles", "Goles", _m(_SUMA >= 4), _CERO),
    "U1.5": ("Menos de 1,5 goles", "Goles", _m(_SUMA <= 1), _CERO),
    "U2.5": ("Menos de 2,5 goles", "Goles", _m(_SUMA <= 2), _CERO),
    "U3.5": ("Menos de 3,5 goles", "Goles", _m(_SUMA <= 3), _CERO),
    "U4.5": ("Menos de 4,5 goles", "Goles", _m(_SUMA <= 4), _CERO),
    "BTTS_SI": ("Ambos equipos marcan", "Ambos marcan", _m((_GH > 0) & (_GA > 0)), _CERO),
    "BTTS_NO": ("No marcan los dos equipos", "Ambos marcan", _m((_GH == 0) | (_GA == 0)), _CERO),
    "L_O0.5": ("{L} marca al menos 1 gol", "Goles por equipo", _m(_GH >= 1), _CERO),
    "V_O0.5": ("{V} marca al menos 1 gol", "Goles por equipo", _m(_GA >= 1), _CERO),
    "L_O1.5": ("{L} marca 2 goles o más", "Goles por equipo", _m(_GH >= 2), _CERO),
    "V_O1.5": ("{V} marca 2 goles o más", "Goles por equipo", _m(_GA >= 2), _CERO),
    "AH1-1.5": ("{L} gana por 2 goles o más (hándicap -1,5)", "Hándicap", _m(_DIF >= 2), _CERO),
    "AH2+1.5": ("{V} no pierde por 2 o más (hándicap +1,5)", "Hándicap", _m(_DIF <= 1), _CERO),
    "AH2-1.5": ("{V} gana por 2 goles o más (hándicap -1,5)", "Hándicap", _m(_DIF <= -2), _CERO),
    "AH1+1.5": ("{L} no pierde por 2 o más (hándicap +1,5)", "Hándicap", _m(_DIF >= -1), _CERO),
    # --- más mercados (como en Betano), todos salen de la misma matriz de marcadores
    **{f"T{k}": (f"Exactamente {k} goles", "Goles exactos", _m(_SUMA == k), _CERO) for k in range(4)},
    "T4+": ("4 goles o más", "Goles exactos", _m(_SUMA >= 4), _CERO),
    "R0-1": ("Entre 0 y 1 gol", "Rango de goles", _m(_SUMA <= 1), _CERO),
    "R2-3": ("Entre 2 y 3 goles", "Rango de goles", _m((_SUMA >= 2) & (_SUMA <= 3)), _CERO),
    "R4-6": ("Entre 4 y 6 goles", "Rango de goles", _m((_SUMA >= 4) & (_SUMA <= 6)), _CERO),
    "R7+": ("7 goles o más", "Rango de goles", _m(_SUMA >= 7), _CERO),
    "PAR": ("Total de goles par", "Par / impar", _m(_SUMA % 2 == 0), _CERO),
    "IMPAR": ("Total de goles impar", "Par / impar", _m(_SUMA % 2 == 1), _CERO),
    "M1_1": ("{L} gana por 1 gol", "Margen de victoria", _m(_DIF == 1), _CERO),
    "M1_2": ("{L} gana por 2 goles", "Margen de victoria", _m(_DIF == 2), _CERO),
    "M1_3+": ("{L} gana por 3 o más", "Margen de victoria", _m(_DIF >= 3), _CERO),
    "M2_1": ("{V} gana por 1 gol", "Margen de victoria", _m(_DIF == -1), _CERO),
    "M2_2": ("{V} gana por 2 goles", "Margen de victoria", _m(_DIF == -2), _CERO),
    "M2_3+": ("{V} gana por 3 o más", "Margen de victoria", _m(_DIF <= -3), _CERO),
    "CS_L": ("{L} no recibe goles (portería a cero)", "Portería a cero", _m(_GA == 0), _CERO),
    "CS_V": ("{V} no recibe goles (portería a cero)", "Portería a cero", _m(_GH == 0), _CERO),
    "WTN_L": ("{L} gana sin recibir gol", "Gana sin recibir gol", _m((_DIF > 0) & (_GA == 0)), _CERO),
    "WTN_V": ("{V} gana sin recibir gol", "Gana sin recibir gol", _m((_DIF < 0) & (_GH == 0)), _CERO),
    **{f"{r}&{b}": (f"{t} y {'ambos marcan' if b == 'SI' else 'no marcan los dos'}", "Resultado y ambos marcan",
                    _m(cr & ((_GH > 0) & (_GA > 0) if b == "SI" else (_GH == 0) | (_GA == 0))), _CERO)
       for r, t, cr in (("1", "Gana {L}", _DIF > 0), ("X", "Empate", _DIF == 0), ("2", "Gana {V}", _DIF < 0))
       for b in ("SI", "NO")},
    **{f"{r}&{o}2.5": (f"{t} y {'más' if o == 'O' else 'menos'} de 2,5 goles", "Resultado y total de goles",
                       _m(cr & (_SUMA >= 3 if o == "O" else _SUMA <= 2)), _CERO)
       for r, t, cr in (("1", "Gana {L}", _DIF > 0), ("X", "Empate", _DIF == 0), ("2", "Gana {V}", _DIF < 0))
       for o in ("O", "U")},
}
_COD = list(MERCADOS)
_W = np.stack([MERCADOS[c][2] for c in _COD])        # (K, MAXG, MAXG)
_P = np.stack([MERCADOS[c][3] for c in _COD])


def probs_mercados(M):
    """M: (S, MAXG, MAXG). Devuelve (gana, devuelve) con forma (S, K)."""
    gana = np.einsum("sij,kij->sk", M, _W)
    dev = np.einsum("sij,kij->sk", M, _P)
    return gana, dev


def liquidar(cod, gl, gv):
    """Resultado de la apuesta con el marcador final: 'gana', 'pierde' o 'devuelve'."""
    w, p = MERCADOS[cod][2], MERCADOS[cod][3]
    gl, gv = min(gl, MAXG - 1), min(gv, MAXG - 1)
    if w[gl, gv]:
        return "gana"
    if p[gl, gv]:
        return "devuelve"
    return "pierde"


# ------------------------------------------------------------------ tarjetas y penales

class ModeloDisciplina:
    """Tarjetas esperadas, gol de penal y expulsión (D22.16 sin efecto árbitro: ESPN no lo publica)."""

    def __init__(self, D, t_ref, vida):
        sel = D.det & (D.t < t_ref)
        self.ok = bool(sel.sum() > 200)
        if not self.ok:
            return
        w = np.exp(-math.log(2) * (t_ref - D.t[sel]) / vida)
        h, a, c = D.h[sel], D.a[sel], D.c[sel]
        nT, nC = D.nT, D.nC
        yh, ya = D.amh[sel], D.ama[sel]
        self.Ch = np.ones(nC)
        self.Ca = np.ones(nC)
        self.recibe = np.ones(nT)
        self.provoca = np.ones(nT)
        k = 8.0
        for _ in range(40):
            lh = self.Ch[c] * self.recibe[h] * self.provoca[a]
            la = self.Ca[c] * self.recibe[a] * self.provoca[h]
            for arr, y, l in ((self.Ch, yh, lh), (self.Ca, ya, la)):
                num = np.bincount(c, w * y, nC)
                den = np.bincount(c, w * l / arr[c], nC)
                glob = num.sum() / max(den.sum(), 1e-9)
                arr[:] = (num + 20 * glob) / (den + 20)
            lh = self.Ch[c] * self.recibe[h] * self.provoca[a]
            la = self.Ca[c] * self.recibe[a] * self.provoca[h]
            G = np.bincount(h, w * yh, nT) + np.bincount(a, w * ya, nT)
            E = np.bincount(h, w * lh / self.recibe[h], nT) + np.bincount(a, w * la / self.recibe[a], nT)
            self.recibe = (G + k) / (E + k)
            lh = self.Ch[c] * self.recibe[h] * self.provoca[a]
            la = self.Ca[c] * self.recibe[a] * self.provoca[h]
            G = np.bincount(a, w * yh, nT) + np.bincount(h, w * ya, nT)
            E = np.bincount(a, w * lh / self.provoca[a], nT) + np.bincount(h, w * la / self.provoca[h], nT)
            self.provoca = (G + k) / (E + k)
        # sobredispersión de tarjetas totales por competición (binomial negativa)
        tot = yh + ya
        self.disp = np.ones(nC)
        for ci in range(nC):
            m = c == ci
            if m.sum() > 60:
                mu, var = tot[m].mean(), tot[m].var()
                self.disp[ci] = max(1.0, var / max(mu, 1e-6))
        # expulsiones y penales: tasa por equipo-partido de cada competición + factor de equipo
        self.tasas = {}
        for nombre, yh_, ya_, kk in (("roja", D.roh[sel], D.roa[sel], 1.5), ("penal", D.peh[sel], D.pea[sel], 2.0)):
            n_c = np.bincount(c, w, nC) * 2
            r_c = np.bincount(c, w * (yh_ + ya_), nC)
            glob = r_c.sum() / max(n_c.sum(), 1e-9)
            tasa_c = (r_c + 30 * glob) / (n_c + 30)
            if nombre == "roja":       # propensión a ser expulsado
                G = np.bincount(h, w * yh_, nT) + np.bincount(a, w * ya_, nT)
            else:                      # propensión a provocar/marcar penales
                G = np.bincount(h, w * yh_, nT) + np.bincount(a, w * ya_, nT)
            E = np.bincount(h, w * tasa_c[c], nT) + np.bincount(a, w * tasa_c[c], nT)
            fac = (G + kk) / (E + kk)
            G2 = np.bincount(a, w * yh_, nT) + np.bincount(h, w * ya_, nT)
            fac2 = (G2 + kk) / (E + kk)  # propensión del rival (conceder penales / provocar rojas)
            self.tasas[nombre] = (tasa_c, glob, fac, fac2, G, E, kk)

    def predecir(self, D, e, rng):
        if not self.ok:
            return None
        c = D.cidx.get(e["lg"])
        if c is None or D.cobertura.get(e["lg"], 0) < 0.7:
            return None
        hi, ai = D.idx[e["h"]["id"]], D.idx[e["a"]["id"]]
        mh = self.Ch[c] * self.recibe[hi] * self.provoca[ai]
        ma = self.Ca[c] * self.recibe[ai] * self.provoca[hi]
        m = mh + ma
        r = self.disp[c]
        lo, hi_ = _nb_intervalo(m, r)
        out = {"esp": round(float(m), 2), "local": round(float(mh), 2), "visita": round(float(ma), 2),
               "ic": [lo, hi_]}
        for nombre in ("roja", "penal"):
            tasa_c, glob, fac, fac2, G, E, kk = self.tasas[nombre]
            lam = tasa_c[c] * (fac[hi] * fac2[ai] + fac[ai] * fac2[hi])
            # incertidumbre: posterior Gamma de los factores de cada equipo
            sh = rng.gamma(G[hi] + kk, 1 / (E[hi] + kk), 300) / fac[hi]
            sa = rng.gamma(G[ai] + kk, 1 / (E[ai] + kk), 300) / fac[ai]
            ps = 1 - np.exp(-lam * np.sqrt(sh * sa))
            out["p_" + nombre] = round(float(1 - math.exp(-lam)), 4)
            out["ic_" + nombre] = [round(float(np.percentile(ps, 10)), 3), round(float(np.percentile(ps, 90)), 3)]
        return out


def _nb_intervalo(m, disp, q=(0.1, 0.9)):
    m = max(float(m), 0.01)
    if disp <= 1.0001:
        # Poisson
        cdf, k, res = 0.0, 0, []
        while len(res) < 2 and k < 40:
            cdf += math.exp(k * math.log(m) - m - math.lgamma(k + 1))
            while len(res) < 2 and cdf >= q[len(res)]:
                res.append(k)
            k += 1
        return res[0], res[1] if len(res) > 1 else k
    rr = m / (disp - 1)        # parámetro de forma
    p = rr / (rr + m)
    cdf, k, res = 0.0, 0, []
    while len(res) < 2 and k < 60:
        lp = math.lgamma(k + rr) - math.lgamma(rr) - math.lgamma(k + 1) + rr * math.log(p) + k * math.log(1 - p)
        cdf += math.exp(lp)
        while len(res) < 2 and cdf >= q[len(res)]:
            res.append(k)
        k += 1
    return res[0], res[1] if len(res) > 1 else k


# ------------------------------------------------------------------ validación (D24.17)

def _metricas(pH, pD, pA, yh, ya):
    oH = (yh > ya).astype(float)
    oD = (yh == ya).astype(float)
    oA = (yh < ya).astype(float)
    rps = 0.5 * ((pH - oH) ** 2 + (pH + pD - oH - oD) ** 2)
    brier = (pH - oH) ** 2 + (pD - oD) ** 2 + (pA - oA) ** 2
    pr = np.clip(pH * oH + pD * oD + pA * oA, 1e-6, 1)
    ll = -np.log(pr)
    elegido = np.argmax(np.stack([pH, pD, pA]), axis=0)
    real = np.argmax(np.stack([oH, oD, oA]), axis=0)
    return rps, brier, ll, (elegido == real).astype(float)


def validar(D, vida, semanas, iters=60, conjunto=False):
    """Prueba retrospectiva semana a semana: ajusta con lo anterior y predice la semana siguiente.
    Con `conjunto=True` se prueba lo mismo que usa la app: en ligas de nivel 3-4, mezcla con memoria corta y Elo
    (todo ajustado solo con partidos anteriores al inicio de cada semana)."""
    from . import conjunto as cj
    fin = D.ahora
    filas = []
    inicial = inicial_c = None
    elo = cj.Elo(D, bordes=[fin - 7 * k for k in range(semanas, 0, -1)]) if conjunto else None
    for k in range(semanas, 0, -1):
        t0 = fin - 7 * k
        t1 = t0 + 7
        entren = D.t < t0
        prueba = np.where((D.t >= t0) & (D.t < t1))[0]
        if entren.sum() < 500 or len(prueba) == 0:
            continue
        P = ajustar(D, entren, t0, vida, iters=iters, inicial=inicial)
        inicial = P
        h, a, c, neu = D.h[prueba], D.a[prueba], D.c[prueba], D.neu[prueba]
        X = {kk: v[prueba] for kk, v in D.X.items()}
        lh, la = _lambdas(P, h, a, c, neu, X)
        if conjunto:
            sel = np.array([cj.aplica(D.comps[ci]) for ci in c])
            if sel.any():
                Pc = ajustar(D, entren, t0, cj.VIDA_CORTA, iters=iters, inicial=inicial_c)
                inicial_c = Pc
                lhS, laS = _lambdas(Pc, h, a, c, neu, X)
                foto, ab = elo.fotos[t0], elo.logit(t0)
                pE = np.array([elo.probs(foto, *ab, h[j], a[j], D.comps[c[j]], bool(neu[j]))[0]
                               for j in np.where(sel)[0]])
                lh, la = lh.copy(), la.copy()
                lh[sel], la[sel] = cj.mezclar(lh[sel], la[sel], lhS[sel], laS[sel], pE, P.rho, matriz,
                                              resumen_matriz)
        M = matriz(lh, la, P.rho)
        pH, pD, pA, _, _ = resumen_matriz(M)
        # referencia ingenua: frecuencias de local/empate/visita de esa competición
        base = {}
        for ci in set(c.tolist()):
            m = entren & (D.c == ci) & (D.t > t0 - 365)
            n = m.sum()
            if n >= 30:
                base[ci] = ((D.yh[m] > D.ya[m]).mean(), (D.yh[m] == D.ya[m]).mean(), (D.yh[m] < D.ya[m]).mean())
        bH = np.array([base.get(ci, (0.45, 0.27, 0.28))[0] for ci in c])
        bD = np.array([base.get(ci, (0.45, 0.27, 0.28))[1] for ci in c])
        bA = 1 - bH - bD
        sdh = np.sqrt(P.var_att[h] + P.var_dfn[a])
        sda = np.sqrt(P.var_att[a] + P.var_dfn[h])
        for j, idx in enumerate(prueba):
            filas.append((D.comps[c[j]], pH[j], pD[j], pA[j], D.yh[idx], D.ya[idx], bH[j], bD[j], bA[j],
                          {"id": D.jugados[idx]["id"], "lh": float(lh[j]), "la": float(la[j]), "sdh": float(sdh[j]),
                           "sda": float(sda[j]), "rho": P.rho, "expo": float(min(P.expo[h[j]], P.expo[a[j]]))}))
    return filas


def resumir_validacion(filas):
    if not filas:
        return None
    lg = np.array([f[0] for f in filas])
    pH, pD, pA, yh, ya, bH, bD, bA = (np.array([f[i] for f in filas], dtype=float) for i in range(1, 9))
    rps, brier, ll, acierto = _metricas(pH, pD, pA, yh, ya)
    brps, bbrier, bll, bac = _metricas(bH, bD, bA, yh, ya)

    def bloque(m):
        return {"n": int(m.sum()), "rps": round(float(rps[m].mean()), 4), "brier": round(float(brier[m].mean()), 4),
                "logloss": round(float(ll[m].mean()), 4), "acierto": round(float(acierto[m].mean()), 4),
                "rps_base": round(float(brps[m].mean()), 4), "acierto_base": round(float(bac[m].mean()), 4)}

    todo = np.ones(len(filas), dtype=bool)
    out = {"global": bloque(todo), "por_liga": {}}
    for l in sorted(set(lg.tolist())):
        m = lg == l
        if m.sum() >= 15:
            out["por_liga"][l] = bloque(m)
    # curva de calibración (todas las salidas 1, X y 2 juntas)
    p = np.concatenate([pH, pD, pA])
    o = np.concatenate([(yh > ya), (yh == ya), (yh < ya)]).astype(float)
    cal = []
    for lo in np.arange(0, 1, 0.1):
        m = (p >= lo) & (p < lo + 0.1)
        if m.sum() >= 20:
            cal.append({"desde": round(float(lo), 1), "pred": round(float(p[m].mean()), 4),
                        "real": round(float(o[m].mean()), 4), "n": int(m.sum())})
    out["calibracion"] = cal
    return out
