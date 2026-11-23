"""Factor de goles reciente.

Por qué existe: la auditoría del 22/11/2026 midió, con 23 340 partidos predichos sin mirar el futuro, que el modelo
se queda corto en goles (esperaba 2,68 por partido y hubo 2,76) y que el desfase crece en las últimas semanas
(cerca de 6 %). Los empates, en cambio, están bien y NO se tocan.

Cómo funciona, en simple: cada semana se compara cuántos goles hubo de verdad con cuántos esperaba el modelo en las
10 semanas ANTERIORES, y esa proporción (acotada entre 0,95 y 1,10) multiplica los goles esperados de la semana
siguiente. Nunca usa partidos futuros.

Control de seguridad: el factor solo se aplica si, probado semana a semana, mejora la predicción de «más de 2,5
goles» y no empeora el 1X2. Si deja de mejorar, se apaga solo (factor 1). Para apagarlo a mano: ACTIVO = False.
"""
import datetime as dt

import numpy as np

ACTIVO = True
VENTANA_SEMANAS = 10
MINIMO_SEMANAS = 4            # con menos historia no se corrige
TOPE = (0.95, 1.10)
TOLERANCIA_1X2 = 0.0002       # lo máximo que puede empeorar el 1X2 para aceptar el factor


def _semana(iso):
    y, w, _ = dt.date.fromisoformat(iso[:10]).isocalendar()
    return y * 100 + w


def _logloss_bin(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def ajustar_validacion(filas, fecha_de, matriz, resumen_matriz):
    """Aplica el factor móvil a las filas de la validación y decide si se usa en las predicciones.

    filas: las de modelo.validar → (liga, pH, pD, pA, goles local, goles visita, bH, bD, bA, info con lh/la/rho/id).
    Devuelve (filas nuevas, resumen). Si el factor no pasa el control, devuelve las filas originales."""
    n = len(filas)
    sem = np.array([_semana(fecha_de.get(f[9]["id"], "1970-01-05")) for f in filas])
    lh = np.array([f[9]["lh"] for f in filas])
    la = np.array([f[9]["la"] for f in filas])
    gh = np.array([f[4] for f in filas], dtype=float)
    ga = np.array([f[5] for f in filas], dtype=float)
    rho = np.array([f[9]["rho"] for f in filas])
    semanas = sorted(set(sem.tolist()))

    def razon(sel):
        esperado = float((lh[sel] + la[sel]).sum())
        return min(TOPE[1], max(TOPE[0], float((gh[sel] + ga[sel]).sum()) / esperado)) if esperado > 0 else 1.0

    c = np.ones(n)
    probada = np.zeros(n, bool)
    for k in range(MINIMO_SEMANAS, len(semanas)):
        actual = sem == semanas[k]
        c[actual] = razon(np.isin(sem, semanas[max(0, k - VENTANA_SEMANAS):k]))
        probada |= actual
    factor = razon(np.isin(sem, semanas[-VENTANA_SEMANAS:])) if len(semanas) >= MINIMO_SEMANAS else 1.0

    def medir(cv):
        ll1 = ll25 = 0.0
        pH, pD, pA, o25 = (np.empty(n) for _ in range(4))
        for r in set(rho.tolist()):                       # la matriz se calcula por bloques con el mismo rho
            s = rho == r
            a, b, d, o, _ = resumen_matriz(matriz(lh[s] * cv[s], la[s] * cv[s], r))
            pH[s], pD[s], pA[s], o25[s] = a, b, d, o["o2.5"]
        y = np.where(gh > ga, 0, np.where(gh == ga, 1, 2))
        p = np.stack([pH, pD, pA], axis=1)[np.arange(n), y]
        if probada.any():
            ll1 = float(-np.log(np.clip(p[probada], 1e-9, 1)).mean())
            ll25 = _logloss_bin(o25[probada], ((gh + ga)[probada] > 2.5).astype(float))
        return pH, pD, pA, ll1, ll25, float(o25[probada].mean()) if probada.any() else None

    _, _, _, l1_sin, l25_sin, o_sin = medir(np.ones(n))
    pH, pD, pA, l1_con, l25_con, o_con = medir(c)
    aplicado = bool(ACTIVO and probada.sum() >= 2000 and l25_con < l25_sin and l1_con <= l1_sin + TOLERANCIA_1X2)
    resumen = {
        "aplicado": aplicado, "factor": round(factor, 4) if aplicado else 1.0, "factor_medido": round(factor, 4),
        "ventana_semanas": VENTANA_SEMANAS, "n": int(probada.sum()),
        "goles_esperados": round(float((lh + la).mean()), 3), "goles_reales": round(float((gh + ga).mean()), 3),
        "logloss_o25": [round(l25_sin, 5), round(l25_con, 5)], "logloss_1x2": [round(l1_sin, 5), round(l1_con, 5)],
        "o25_pred": [None if o_sin is None else round(o_sin, 4), None if o_con is None else round(o_con, 4)],
        "o25_real": round(float(((gh + ga)[probada] > 2.5).mean()), 4) if probada.any() else None,
    }
    if not aplicado:
        return filas, resumen
    nuevas = []
    for j, f in enumerate(filas):
        info = dict(f[9], lh=float(lh[j] * c[j]), la=float(la[j] * c[j]), factor_goles=float(c[j]))
        nuevas.append((f[0], float(pH[j]), float(pD[j]), float(pA[j]), f[4], f[5], f[6], f[7], f[8], info))
    return nuevas, resumen


def texto(resumen):
    """Frase corta para el registro y la app."""
    if not resumen:
        return "factor de goles: sin evaluar"
    if not resumen["aplicado"]:
        return f"factor de goles NO aplicado (medido {resumen['factor_medido']}: no mejora la prueba)"
    return (f"factor de goles {resumen['factor']} aplicado: «más de 2,5» pasa de {resumen['o25_pred'][0]} a "
            f"{resumen['o25_pred'][1]} (real {resumen['o25_real']}) en {resumen['n']} partidos de prueba")
