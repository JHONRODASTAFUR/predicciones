"""Conjunto de modelos para las ligas de nivel 3-4 (el «resto del mundo»).

Qué hace, en simple:
  El modelo principal (Dixon-Coles, memoria larga) calcula los goles esperados de cada equipo. En las ligas
  chicas tiene pocos datos y reacciona lento, así que se combina con dos opiniones más:
    1. El MISMO modelo con memoria corta (vida media de 120 días): reacciona rápido a la racha actual.
    2. Un rating ELO por diferencia de goles: se actualiza partido a partido y conecta ligas entre sí a través
       de las copas (Libertadores, Champions, copas nacionales...).
  El TOTAL de goles esperados sale siempre del modelo principal (es el mejor para más/menos goles);
  la mezcla solo decide cómo se REPARTEN esos goles entre local y visita.

Evidencia (prueba semana a semana sin mirar el futuro, 26 semanas, 8 843 partidos de nivel 3-4):
  RPS 1X2 mejora ~0,0019 (≈0,9 %), intervalo 90 % siempre negativo; más/menos 2,5 igual. En ligas de nivel
  1-2 no mejoraba, por eso allí NO se aplica.
"""
from __future__ import annotations

import math

import numpy as np

from .config import INFO_LIGA

VIDA_CORTA = 120          # días de vida media del modelo de memoria corta
PESO_CORTA = 0.20         # peso (en logaritmos) del modelo de memoria corta
PESO_ELO = 0.45           # peso del Elo; el modelo principal se queda con el resto (0,35)
NIVEL_MIN = 3             # se aplica solo a ligas de nivel 3 y 4
K_ELO = 30.0              # cuánto se mueve el Elo por partido
LOCALIA_ELO = 60.0        # ventaja inicial de jugar en casa (puntos Elo); luego se aprende por liga
VENTANA_LOGIT = 730       # días de historia para traducir diferencia de Elo a probabilidades


def aplica(lg):
    return INFO_LIGA.get(lg, {}).get("nivel", 4) >= NIVEL_MIN


# ------------------------------------------------------------------ Elo

class Elo:
    """Recorre los partidos jugados en orden de fecha. Guarda «fotos» de los ratings en las fechas pedidas
    (para la prueba semana a semana) y el historial (diferencia previa, resultado) para el logit."""

    def __init__(self, D, bordes=()):
        n = len(D.jugados)
        r = np.full(D.nT, np.nan)
        H = {}
        liga_de = {}                            # equipo -> su liga (la última liga regular donde jugó)
        suma, cuenta = {}, {}                   # suma y número de ratings por liga (para equipos nuevos)
        bordes = sorted(bordes)
        self.fotos, bi = {}, 0
        self.t_hist = D.t.copy()
        self.dr_hist = np.zeros(n)
        self.y_hist = np.where(D.yh > D.ya, 0, np.where(D.yh == D.ya, 1, 2))
        tipos = {lg: INFO_LIGA.get(lg, {}).get("tipo") for lg in D.comps}

        def mover_liga(i, lg):
            viejo = liga_de.get(i)
            if viejo == lg:
                return
            if viejo is not None:
                suma[viejo] -= r[i]
                cuenta[viejo] -= 1
            liga_de[i] = lg
            suma[lg] = suma.get(lg, 0.0) + r[i]
            cuenta[lg] = cuenta.get(lg, 0) + 1

        for k in range(n):
            t = D.t[k]
            while bi < len(bordes) and t >= bordes[bi]:
                self.fotos[bordes[bi]] = (r.copy(), dict(H), dict(suma), dict(cuenta))
                bi += 1
            hi, ai, lg = D.h[k], D.a[k], D.comps[D.c[k]]
            for i in (hi, ai):
                if np.isnan(r[i]):
                    r[i] = suma[lg] / cuenta[lg] if cuenta.get(lg, 0) >= 4 else 1500.0
            neu = bool(D.neu[k])
            h = 0.0 if neu else H.get(lg, LOCALIA_ELO)
            dr = r[hi] - r[ai] + h
            self.dr_hist[k] = dr
            eh = 1.0 / (1.0 + 10 ** (-dr / 400.0))
            gh, ga = D.yh[k], D.ya[k]
            s = 1.0 if gh > ga else 0.5 if gh == ga else 0.0
            gd = abs(gh - ga)
            g = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11.0 + gd) / 8.0
            d = K_ELO * g * (s - eh)
            for i, sig in ((hi, 1.0), (ai, -1.0)):
                if liga_de.get(i) is not None:
                    suma[liga_de[i]] += sig * d
                r[i] += sig * d
            if not neu:
                H[lg] = H.get(lg, LOCALIA_ELO) + 0.075 * K_ELO * (s - eh)
            if tipos.get(lg) == "liga":
                mover_liga(hi, lg)
                mover_liga(ai, lg)
        while bi < len(bordes):
            self.fotos[bordes[bi]] = (r.copy(), dict(H), dict(suma), dict(cuenta))
            bi += 1
        self.final = (r, H, suma, cuenta)
        self._logits = {}

    def logit(self, t0):
        """Traduce diferencia de Elo a 1X2: P(1)=σ(a·dr−θ), P(2)=σ(−a·dr−θ). Se ajusta con los partidos
        de los últimos 2 años ANTES de t0 (rejilla gruesa + fina, sin librerías extra)."""
        clave = round(float(t0), 3)
        if clave in self._logits:
            return self._logits[clave]
        sel = (self.t_hist < t0) & (self.t_hist >= t0 - VENTANA_LOGIT)
        dr, y = self.dr_hist[sel][-40000:], self.y_hist[sel][-40000:]
        if len(y) < 500:
            self._logits[clave] = (0.0045, 0.55)
            return self._logits[clave]

        def ll(a, th):
            p1 = 1 / (1 + np.exp(-(a * dr - th)))
            p2 = 1 / (1 + np.exp(-(-a * dr - th)))
            P = np.stack([p1, np.clip(1 - p1 - p2, 1e-6, 1), p2], 1)
            return np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1)).sum()
        mejor = max(((a, th) for a in np.linspace(0.002, 0.008, 13) for th in np.linspace(0.2, 1.0, 17)),
                    key=lambda p: ll(*p))
        a0, t0_ = mejor
        mejor = max(((a, th) for a in np.linspace(a0 - 0.00025, a0 + 0.00025, 5)
                     for th in np.linspace(t0_ - 0.025, t0_ + 0.025, 5)), key=lambda p: ll(*p))
        self._logits[clave] = (float(mejor[0]), float(mejor[1]))
        return self._logits[clave]

    @staticmethod
    def rating(foto, i, lg):
        r, _, suma, cuenta = foto
        if i < len(r) and not np.isnan(r[i]):
            return float(r[i])
        return suma[lg] / cuenta[lg] if cuenta.get(lg, 0) >= 4 else 1500.0

    def probs(self, foto, a, th, hi, ai, lg, neutral):
        """1X2 del Elo para un partido (y la diferencia de rating usada)."""
        h = 0.0 if neutral else foto[1].get(lg, LOCALIA_ELO)
        dr = self.rating(foto, hi, lg) - self.rating(foto, ai, lg) + h
        p1 = 1 / (1 + math.exp(-(a * dr - th)))
        p2 = 1 / (1 + math.exp(-(-a * dr - th)))
        px = max(1 - p1 - p2, 1e-4)
        s = p1 + px + p2
        return (p1 / s, px / s, p2 / s), dr


# ------------------------------------------------------------------ mezcla

def lambdas_elo(pE, total, rho, matriz, resumen_matriz, it=40):
    """Con el total de goles dado, el reparto local/visita cuyo P(1)−P(2) coincide con el del Elo (bisección)."""
    pE = np.atleast_2d(np.asarray(pE, float))
    total = np.atleast_1d(np.asarray(total, float))
    d = pE[:, 0] - pE[:, 2]
    lo, hi = np.full(len(total), 0.02), np.full(len(total), 0.98)
    for _ in range(it):
        s = (lo + hi) / 2
        pH, _, pA, _, _ = resumen_matriz(matriz(s * total, (1 - s) * total, rho))
        mayor = (pH - pA) > d
        hi = np.where(mayor, s, hi)
        lo = np.where(mayor, lo, s)
    s = (lo + hi) / 2
    return s * total, (1 - s) * total


def mezclar(lh, la, lhS, laS, pE, rho, matriz, resumen_matriz):
    """Goles esperados finales: total del modelo principal; reparto = mezcla geométrica de los tres modelos."""
    lh, la, lhS, laS = (np.atleast_1d(np.asarray(x, float)) for x in (lh, la, lhS, laS))
    total = lh + la
    lhE, laE = lambdas_elo(pE, total, rho, matriz, resumen_matriz)
    w0 = 1 - PESO_CORTA - PESO_ELO
    mh = np.exp(w0 * np.log(lh) + PESO_CORTA * np.log(lhS) + PESO_ELO * np.log(lhE))
    ma = np.exp(w0 * np.log(la) + PESO_CORTA * np.log(laS) + PESO_ELO * np.log(laE))
    s = mh / (mh + ma)
    return s * total, (1 - s) * total


class Conjunto:
    """Se cuelga del modelo principal (P.conj) y lo usa modelo.lambdas_partido para los partidos de nivel 3-4."""

    def __init__(self, D, P_corta, t_ref):
        self.Pc = P_corta
        self.elo = Elo(D)
        self.foto = self.elo.final
        self.ab = self.elo.logit(t_ref)

    def ajustar(self, D, e, lh, la, rho, lambdas_partido, matriz, resumen_matriz):
        if not aplica(e["lg"]):
            return lh, la, None
        Ls = lambdas_partido(self.Pc, D, e)
        hi, ai = D.idx[e["h"]["id"]], D.idx[e["a"]["id"]]
        pE, dr = self.elo.probs(self.foto, *self.ab, hi, ai, e["lg"], bool(e.get("neutral")))
        nh, na = mezclar(lh, la, Ls["lh"], Ls["la"], [pE], rho, matriz, resumen_matriz)
        info = {"lh_principal": round(lh, 3), "la_principal": round(la, 3),
                "lh_corta": round(Ls["lh"], 3), "la_corta": round(Ls["la"], 3),
                "elo_1x2": [round(x, 3) for x in pE], "elo_dif": round(dr, 1),
                "pesos": {"principal": round(1 - PESO_CORTA - PESO_ELO, 2), "corta": PESO_CORTA, "elo": PESO_ELO}}
        return float(nh[0]), float(na[0]), info
