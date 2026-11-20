"""Motor de simulación Monte Carlo: juega cada partido 10 000 veces.

Cómo funciona, en simple:
  1. Recibe la matriz de marcadores del modelo (la probabilidad de cada resultado 0-0, 1-0, 2-1…).
  2. «Juega» el partido N veces. En cada partido simulado sortea el marcador, reparte los goles entre primer y
     segundo tiempo, decide quién marcó primero y sortea córners y tarjetas.
  3. Como todo sale del MISMO partido simulado, mide cosas que la matriz sola no da (ganar alguna mitad,
     escenarios de dominio) y se comprueba a sí misma.

No inventa nada: las medias de entrada vienen del modelo ya validado con partidos pasados.
Controles de calidad incluidos en cada simulación:
  · Intervalo del 95 % de cada probabilidad (el error propio del sorteo).
  · Prueba de estabilidad: compara 10 lotes de 1 000 partidos entre sí y contra el valor exacto de la matriz.
  · Semilla fija por partido: la misma entrada da siempre el mismo resultado (se puede repetir y auditar).
"""
import math
import zlib

import numpy as np

N_SIM = 10_000
LOTES = 10
LINEAS_CORNERS = (5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5)
LINEAS_TARJETAS = (2.5, 3.5, 4.5, 5.5, 6.5)
CLAVES_CONTROL = ("1", "X", "2", "O2.5", "BTTS_SI")      # las que se comparan con el valor exacto


def semilla_de(id_partido):
    """Semilla estable para cada partido (no depende de la hora ni de la máquina)."""
    return zlib.crc32(str(id_partido).encode("utf-8"))


def wilson(aciertos, n, z=1.96):
    """Intervalo de Wilson del 95 % para una proporción (mejor que ±1,96·error cuando p está cerca de 0 o 1)."""
    if n <= 0:
        return 0.0, 1.0
    p = aciertos / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    mitad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centro - mitad), min(1.0, centro + mitad)


def exactas_de(M):
    """Probabilidades exactas de la matriz, para controlar la simulación."""
    M = np.asarray(M, dtype=float)
    M = M / M.sum()
    i = np.arange(M.shape[0])[:, None]
    j = np.arange(M.shape[1])[None, :]
    return {"1": float(M[i > j].sum()), "X": float(M[i == j].sum()), "2": float(M[i < j].sum()),
            "O2.5": float(M[i + j > 2.5].sum()), "BTTS_SI": float(M[1:, 1:].sum())}


def _conteo(rng, media, phi, n):
    """Córners o tarjetas: Poisson, o binomial negativa (Poisson con media gamma) si hay sobredispersión."""
    media = max(float(media), 1e-6)
    if phi and phi > 1.02:
        return rng.poisson(rng.gamma(media / (phi - 1), phi - 1, n))
    return rng.poisson(media, n)


def sortear(M, n, rng, prop_1t=None, corners=None, tarjetas=None):
    """Los n partidos simulados, como vectores (una posición por partido)."""
    M = np.asarray(M, dtype=float)
    G = M.shape[1]
    p = M.ravel() / M.sum()
    celdas = rng.choice(p.size, size=n, p=p)
    S = {"gh": celdas // G, "ga": celdas % G}
    tot = S["gh"] + S["ga"]
    # quién marca primero: los goles del partido se ordenan al azar (ritmo de gol constante durante el partido)
    S["primero_local"] = rng.random(n) * np.maximum(tot, 1) < S["gh"]
    if prop_1t:
        S["h1"] = rng.binomial(S["gh"], prop_1t)        # cada gol cae en el primer tiempo con probabilidad prop_1t
        S["a1"] = rng.binomial(S["ga"], prop_1t)
    for nombre, c in (("cor", corners), ("tarj", tarjetas)):
        if c and c.get("local") is not None and c.get("visita") is not None:
            S[nombre + "_h"] = _conteo(rng, c["local"], c.get("phi") or 1.0, n)
            S[nombre + "_a"] = _conteo(rng, c["visita"], c.get("phi") or 1.0, n)
    return S


def eventos(S):
    """De los partidos simulados a «¿ocurrió o no?» para cada mercado (vectores de verdadero/falso)."""
    gh, ga = S["gh"], S["ga"]
    tot, dif = gh + ga, gh - ga
    E = {"1": dif > 0, "X": dif == 0, "2": dif < 0, "BTTS_SI": (gh > 0) & (ga > 0),
         "PRIMERO_L": S["primero_local"], "PRIMERO_V": (tot > 0) & ~S["primero_local"], "PRIMERO_NADIE": tot == 0,
         # escenarios de dominio
         "DOM_L": dif >= 2, "DOM_V": dif <= -2, "CERRADO": np.abs(dif) <= 1, "ABIERTO": tot >= 4, "TRABADO": tot <= 1}
    for x in (0.5, 1.5, 2.5, 3.5):
        E[f"O{x}"] = tot > x
    if "h1" in S:
        h1, a1 = S["h1"], S["a1"]
        h2, a2 = gh - h1, ga - a1
        d1, d2 = h1 - a1, h2 - a2
        E.update({"1T_1": d1 > 0, "1T_X": d1 == 0, "1T_2": d1 < 0, "2T_1": d2 > 0, "2T_X": d2 == 0, "2T_2": d2 < 0,
                  "1T_O0.5": h1 + a1 > 0, "1T_O1.5": h1 + a1 > 1, "2T_O0.5": h2 + a2 > 0, "2T_O1.5": h2 + a2 > 1,
                  "1T_BTTS": (h1 > 0) & (a1 > 0), "2T_BTTS": (h2 > 0) & (a2 > 0),
                  # ganador de cualquier mitad
                  "MIT_L": (d1 > 0) | (d2 > 0), "MIT_V": (d1 < 0) | (d2 < 0),
                  "MIT_L_AMBAS": (d1 > 0) & (d2 > 0), "MIT_V_AMBAS": (d1 < 0) & (d2 < 0),
                  "MIT_EMPATES": (d1 == 0) & (d2 == 0)})
        E["MIT_ALGUNO_AMBAS"] = E["MIT_L_AMBAS"] | E["MIT_V_AMBAS"]
    for pref, lineas in (("cor", LINEAS_CORNERS), ("tarj", LINEAS_TARJETAS)):
        if pref + "_h" in S:
            a, b = S[pref + "_h"], S[pref + "_a"]
            for x in lineas:
                E[f"{pref}_O{x}"] = a + b > x
            E[f"{pref}_1"], E[f"{pref}_X"], E[f"{pref}_2"] = a > b, a == b, a < b
    return E


def simular(M, id_partido="", n=N_SIM, prop_1t=None, corners=None, tarjetas=None):
    """Simula el partido n veces y devuelve probabilidades, intervalos y la prueba de estabilidad."""
    semilla = semilla_de(id_partido)
    rng = np.random.default_rng(semilla)
    S = sortear(M, n, rng, prop_1t, corners, tarjetas)
    E = eventos(S)
    p = {k: float(v.mean()) for k, v in E.items()}

    # prueba de estabilidad: 10 lotes de n/10 partidos y comparación con el valor exacto de la matriz
    tam = n // LOTES
    exactas = exactas_de(M)
    desv = max(float(np.abs(E[k][:tam * LOTES].reshape(LOTES, tam).mean(axis=1) - p[k]).max()) for k in CLAVES_CONTROL)
    error = max(abs(p[k] - exactas[k]) for k in CLAVES_CONTROL)
    tolerancia = 3 * math.sqrt(0.25 / n)                 # 3 errores estándar en el peor caso (p = 50 %)

    marc, veces = np.unique(S["gh"] * 100 + S["ga"], return_counts=True)
    top = sorted(zip(veces.tolist(), marc.tolist()), reverse=True)[:5]
    medias = {"goles": float((S["gh"] + S["ga"]).mean())}
    for pref in ("cor", "tarj"):
        if pref + "_h" in S:
            medias[pref] = float((S[pref + "_h"] + S[pref + "_a"]).mean())
    return {
        "n": n, "semilla": semilla,
        "p": {k: round(v, 4) for k, v in p.items()},
        "ic": {k: [round(x, 4) for x in wilson(int(E[k].sum()), n)] for k in CLAVES_CONTROL},
        "medias": {k: round(v, 2) for k, v in medias.items()},
        "marcadores": [[m // 100, m % 100, round(v / n, 4)] for v, m in top],
        "estabilidad": {"lotes": LOTES, "desv_lotes": round(desv, 4), "error_vs_exacto": round(error, 4),
                        "tolerancia": round(tolerancia, 4), "estable": bool(error <= tolerancia)},
    }
