"""Apuesta recomendada para cada partido (R17-R20, D24.15, D24.17).

Evidencia que fija el diseño (prueba retrospectiva con 6.459 partidos y cuotas reales de cierre):
- La cuota de cierre de la casa predijo mejor que el modelo (log-loss 0,9995 frente a 1,0246) y mezclar
  el modelo con la cuota no mejoró nada (peso óptimo del modelo = 0).
- Apostar "donde el modelo ve valor" perdió entre 13% y 15% de lo apostado; apostar al favorito del modelo
  perdió 7,8% (≈ el margen de la casa, 7,3%).
Por eso:
1) Cuando hay cuotas, las probabilidades de TODOS los mercados se calculan con los goles esperados que
   reproducen la cuota sin margen (el mercado es más preciso). Sin cuotas, se usa el modelo.
2) En cada partido se recomienda la opción más segura (mayor probabilidad conservadora p10) dentro del
   rango de cuotas del perfil elegido, con su "cuota mínima": solo conviene si la casa paga eso o más.
3) La etiqueta "apuesta de valor" queda APAGADA mientras la prueba con cuotas reales no muestre ganancia
   con un intervalo del 80% por encima de cero; se vuelve a comprobar cada día.
"""
import json
import math

import numpy as np

from .config import DATOS
from .modelo import MAXG, MERCADOS, _COD, liquidar, matriz, probs_mercados

ARCHIVO_LAMBDAS = DATOS / "lambdas_mercado.json"

PERFILES = {   # rangos de probabilidad (especificación del juez del panel)
    "conservador": {"nombre": "Más probable", "rango": (0.75, 0.88), "tope": 0.5,
                    "texto": "Aciertas más seguido, pero paga poco."},          # cuota justa 1,14-1,33
    "equilibrado": {"nombre": "Recomendada", "rango": (0.55, 0.75), "tope": 0.5,
                    "texto": "Equilibrio entre acierto y pago."},               # cuota justa 1,33-1,82
    "arriesgado": {"nombre": "Mayor pago", "rango": (0.35, 0.55), "tope": 0.25,
                   "texto": "Aciertas menos, pero paga más."},                  # cuota justa 1,82-2,86
}
RESPALDOS = [(0.45, 0.75), (0.40, 0.85), (0.30, 0.90)]   # si el rango del perfil queda vacío
PERFIL_POR_DEFECTO = "equilibrado"
PISO_CUOTA = 1.25              # por debajo la ganancia es casi toda ruido
VENTAJA_MODELO, VENTAJA_MERCADO = 0.04, 0.03
KAPPA_INICIAL, KAPPA_VALIDADO = 1.0, 0.5   # escenario prudente = p − κ·(p − p10)
SESGO_MAX = 0.015              # un mercado no puede ganar más de 1,5 puntos menos de lo que promete
# mercados que pueden ser la apuesta principal: nivel de complejidad (penaliza el orden, no la probabilidad)
CANDIDATOS = {"1": 0, "X": 0, "2": 0, "1X": 0, "X2": 0, "O1.5": 0, "O2.5": 0, "O3.5": 0, "U1.5": 0, "U2.5": 0,
              "U3.5": 0, "BTTS_SI": 0, "BTTS_NO": 0, "DNB1": 1, "DNB2": 1, "L_O0.5": 1, "V_O0.5": 1,
              "L_O1.5": 1, "V_O1.5": 1, "AH1-1.5": 2, "AH2+1.5": 2, "AH2-1.5": 2, "AH1+1.5": 2}
ORDEN_FAMILIA = ["1X2", "Doble oportunidad", "Goles", "Ambos marcan", "Empate no válido", "Goles por equipo",
                 "Hándicap"]
PENALIZACION_NIVEL = 0.01
EMPATE_PUNTAJE = 0.01
ANCHO_MAX = 0.25               # intervalo p90-p10 máximo para ser candidata (solo con el modelo)
ANCHO_AVISO = 0.20
HISTERESIS = 0.02              # la recomendación anterior se mantiene salvo mejora clara
ECE_MAX = 0.03                 # calibración mínima de un mercado en la prueba para ser candidato
N_MIN_PUERTA = 1000
KELLY_FRACCION = 0.25          # un cuarto de Kelly con la probabilidad prudente...
FACTOR_RIESGO = {"bajo": 1.0, "medio": 0.5, "alto": 0.0}   # ...con tope del perfil × este factor
EV_MIN_VALOR = 0.03
P_MIN_VALOR = 0.25
CUOTA_MAX_VALOR = 5.0
_IDX = {c: k for k, c in enumerate(_COD)}


# ---------------------------------------------------------------- matemáticas básicas

METODO_DESMARGINADO = {"metodo": "proporcional"}   # se cambia a "potencia" si la prueba lo justifica


def desmarginar(cuotas, metodo=None):
    """Probabilidades implícitas sin el margen de la casa (D24.15).

    proporcional: p_i = (1/o_i) / Σ(1/o_i).
    potencia: p_i = (1/o_i)^k con k tal que Σ p_i = 1 (corrige el sesgo favorito-sorpresa).
    """
    inv = np.array([1 / c for c in cuotas], dtype=float)
    margen = float(inv.sum() - 1)
    metodo = metodo or METODO_DESMARGINADO["metodo"]
    if metodo == "potencia" and margen > 0:
        lo, hi = 1.0, 10.0
        for _ in range(60):
            k = (lo + hi) / 2
            if (inv ** k).sum() > 1:
                lo = k
            else:
                hi = k
        p = inv ** ((lo + hi) / 2)
        return p / p.sum(), margen
    return inv / inv.sum(), margen


def ev(p_gana, p_dev, cuota):
    """Valor esperado por unidad apostada (cuota decimal; 'devuelve' = se recupera lo apostado)."""
    return p_gana * (cuota - 1) - (1 - p_gana - p_dev)


def kelly(p_gana, p_dev, cuota):
    """Fracción de Kelly con posible devolución: (p_g·b − p_p) / (b·(p_g + p_p))."""
    p_pierde = 1 - p_gana - p_dev
    b = cuota - 1
    if b <= 0 or p_gana + p_pierde <= 0:
        return 0.0
    return max(0.0, (p_gana * b - p_pierde) / (b * (p_gana + p_pierde)))


def tope_stake(perfil, riesgo):
    """Máximo % de la banca para una apuesta: tope del perfil × factor del riesgo (alto = 0)."""
    return PERFILES[perfil]["tope"] * FACTOR_RIESGO[riesgo]


def stake_pct(p_prud, p_dev, cuota, perfil="equilibrado", riesgo="bajo"):
    """% de la banca: ¼ de Kelly con la probabilidad prudente, en pasos de 0,25%; por debajo de 0,25%, nada."""
    pg = p_prud * (1 - p_dev)
    bruto = min(tope_stake(perfil, riesgo), 100 * KELLY_FRACCION * kelly(pg, p_dev, cuota))
    return math.floor(bruto / 0.25 + 1e-9) * 0.25


def prudente(p, p10, base, kappa=KAPPA_INICIAL, peso_modelo=0.0):
    """Probabilidad del 'escenario prudente' con la que se decide: p − κ·(p − p10).

    Solo modelo: κ = 1 (p10) hasta que la prueba muestre que las recomendaciones están calibradas; entonces 0,5.
    Con cuotas: la incertidumbre del modelo solo cuenta en la parte que pesa el modelo (hoy 0%).
    """
    if base == "mercado":
        return p - kappa * peso_modelo * (p - p10)
    return p - kappa * (p - p10)


def _techo(x):
    return math.ceil(x * 100 - 1e-9) / 100


def cuota_minima(p, p_prud, base="modelo"):
    """Menor cuota con ventaja ≥ máx(e0; 2%·cuota) con p, que aguanta 2 puntos de error, no pierde en el
    escenario prudente y supera el piso de 1,25. e0 = 4% solo con el modelo, 3% con cuotas de referencia."""
    e0 = VENTAJA_MERCADO if base == "mercado" else VENTAJA_MODELO
    c = max(PISO_CUOTA, (1 + e0) / max(p, 1e-6), 1 / max(p - 0.02, 1e-6), 1 / max(p_prud, 1e-6))
    return _techo(c)


def cuota_alerta(p, minima):
    """Si la casa paga esto o más, probablemente sabe algo que el modelo no (lesión, alineación)."""
    return _techo(max(1.25 / max(p, 1e-6), minima + 0.15))


PESO_LINEA_GOLES = 0.1   # el 1X2 manda; la línea de goles solo desempata (Dixon-Coles no siempre cumple ambas)


def lambdas_de_mercado(p1x2, rho, p_over=None, linea=None):
    """Goles esperados (local, visita) que reproducen el 1X2 sin margen (rejilla de dos pasadas).

    Devuelve (lh, la, error RMS del 1X2): si el error es grande, las cuotas de la casa no son coherentes con un
    reparto de goles Poisson/Dixon-Coles y la recomendación lo avisa.
    """
    objetivo = np.asarray(p1x2, dtype=float)
    tot = np.arange(MAXG)[:, None] + np.arange(MAXG)[None, :]

    def error(lh, la):
        M = matriz(lh, la, rho)
        g, _ = probs_mercados(M)
        e1x2 = ((g[:, :3] - objetivo[None, :]) ** 2).sum(axis=1)
        e = e1x2
        if p_over is not None and linea is not None:
            e = e + PESO_LINEA_GOLES * ((M * (tot > linea)).sum(axis=(1, 2)) - p_over) ** 2
        return e, e1x2

    rej = np.linspace(0.15, 4.5, 45)
    LH, LA = np.meshgrid(rej, rej, indexing="ij")
    e, _ = error(LH.ravel(), LA.ravel())
    i = int(np.argmin(e))
    bh, ba = LH.ravel()[i], LA.ravel()[i]
    fin = np.linspace(-0.1, 0.1, 21)
    LH, LA = np.meshgrid(np.maximum(bh + fin, 0.05), np.maximum(ba + fin, 0.05), indexing="ij")
    e, e1x2 = error(LH.ravel(), LA.ravel())
    i = int(np.argmin(e))
    return float(LH.ravel()[i]), float(LA.ravel()[i]), float(math.sqrt(e1x2[i] / 3))


def _efectiva(g, d):
    """Probabilidad de ganar descontando las devoluciones (la que define la cuota justa)."""
    return g / np.maximum(1 - d, 1e-9)


# ---------------------------------------------------------------- recalibración por mercado (D24.17)
# Se corrigen solo los mercados "primarios"; su complemento se deriva para que sumen 100%.
COMPLEMENTO = {"U1.5": "O1.5", "U2.5": "O2.5", "U3.5": "O3.5", "BTTS_NO": "BTTS_SI", "AH2+1.5": "AH1-1.5",
               "AH1+1.5": "AH2-1.5", "DNB2": "DNB1", "1X": "2", "X2": "1", "12": "X"}
# El 1X2 (y lo que deriva de él) ya sale bien calibrado: solo se corrigen los mercados de goles.
PRIMARIOS = ["O1.5", "O2.5", "O3.5", "U4.5", "BTTS_SI", "L_O0.5", "V_O0.5", "L_O1.5", "V_O1.5", "AH1-1.5", "AH2-1.5"]
TRAMO = 0.05
CENTROS = np.arange(TRAMO / 2, 1, TRAMO)
CORRECCION_MAX = 0.05
PRIOR_TRAMO = 200


def ajustar_mapa(ps, ok):
    """Corrección (real − predicho) por tramo de 5 puntos, encogida hacia 0 si hay pocos datos."""
    corr = []
    for c in CENTROS:
        t = (ps >= c - TRAMO / 2) & (ps < c + TRAMO / 2)
        n = int(t.sum())
        if n == 0:
            corr.append(0.0)
            continue
        s = n / (n + PRIOR_TRAMO)
        corr.append(float(np.clip(s * (ok[t].mean() - ps[t].mean()), -CORRECCION_MAX, CORRECCION_MAX)))
    return [round(x, 4) for x in corr]


def recalibrar(p, mapas):
    """Aplica los mapas (por código primario) a un vector de probabilidades efectivas de los 25 mercados."""
    if not mapas:
        return p.copy()
    q = p.copy()
    for c in PRIMARIOS:
        m = mapas.get(c)
        if m:
            k = _IDX[c]
            q[k] = np.clip(p[k] + np.interp(p[k], CENTROS, m), 0.001, 0.999)
    # coherencia entre mercados anidados (cada corrección se aprendió por separado)
    i = _IDX
    q[i["O2.5"]] = min(q[i["O2.5"]], q[i["O1.5"]])
    q[i["O3.5"]] = min(q[i["O3.5"]], q[i["O2.5"]])
    q[i["U4.5"]] = max(q[i["U4.5"]], 1 - q[i["O3.5"]])
    q[i["L_O1.5"]] = min(q[i["L_O1.5"]], q[i["L_O0.5"]])
    q[i["V_O1.5"]] = min(q[i["V_O1.5"]], q[i["V_O0.5"]])
    q[i["BTTS_SI"]] = min(q[i["BTTS_SI"]], q[i["L_O0.5"]], q[i["V_O0.5"]])
    q[i["AH1-1.5"]] = min(q[i["AH1-1.5"]], q[i["L_O1.5"]], q[i["1"]])
    q[i["AH2-1.5"]] = min(q[i["AH2-1.5"]], q[i["V_O1.5"]], q[i["2"]])
    for c, prim in COMPLEMENTO.items():
        if prim in PRIMARIOS:
            q[_IDX[c]] = 1 - q[_IDX[prim]]
    return q


# ---------------------------------------------------------------- selección

def _puntaje(k, prud):
    return prud[k] - PENALIZACION_NIVEL * CANDIDATOS[_COD[k]]


def _clave_orden(k, p, prud):
    fam = MERCADOS[_COD[k]][1]
    return (round(_puntaje(k, prud) / EMPATE_PUNTAJE), p[k], -CANDIDATOS[_COD[k]],
            -ORDEN_FAMILIA.index(fam) if fam in ORDEN_FAMILIA else -99)


def elegir(p, prud, ancho, perfil, excluidos=(), previo=None):
    """Opción con mayor probabilidad prudente (con penalización por complejidad) dentro del rango del perfil.

    `ancho` es el intervalo p90-p10 que cuenta (0 cuando manda el mercado). Si el rango queda vacío se
    amplía (respaldo). Devuelve (índice, usó_respaldo).
    """
    permitidos = [k for k, c in enumerate(_COD) if c in CANDIDATOS and c not in excluidos]
    validos = [k for k in permitidos if ancho[k] <= ANCHO_MAX] or permitidos   # sin opciones: se relaja el ancho
    for n, (lo, hi) in enumerate([PERFILES[perfil]["rango"]] + RESPALDOS):
        idx = [k for k in validos if lo <= p[k] <= hi]
        if idx:
            mejor = max(idx, key=lambda k: _clave_orden(k, p, prud))
            if previo in _IDX and _IDX[previo] in idx and n == 0:
                kp = _IDX[previo]
                if _puntaje(mejor, prud) - _puntaje(kp, prud) < HISTERESIS:
                    mejor = kp     # estabilidad entre actualizaciones horarias
            return mejor, n > 0, False
    # ninguna opción en ningún rango: se muestra la más probable, marcada como "mejor no apostar"
    return max(validos or range(len(p)), key=lambda k: prud[k]), True, True


def fijar_mercado(p, pk, po=None, linea=None):
    """Las opciones que la casa cotiza, o que salen directo de su 1X2, usan su probabilidad exacta sin margen
    (el ajuste de goles esperados deja un residuo); el resto se mantiene coherente con ellas."""
    q = p.copy()
    i = _IDX
    q[i["1"]], q[i["X"]], q[i["2"]] = pk
    q[i["1X"]], q[i["X2"]], q[i["12"]] = pk[0] + pk[1], pk[1] + pk[2], pk[0] + pk[2]
    q[i["DNB1"]] = pk[0] / max(pk[0] + pk[2], 1e-9)
    q[i["DNB2"]] = 1 - q[i["DNB1"]]
    if po is not None and linea is not None and f"O{linea}" in i:
        q[i[f"O{linea}"]] = po[0]
        for otra in (1.5, 2.5, 3.5):            # más goles = menos probable
            if otra < linea:
                q[i[f"O{otra}"]] = max(q[i[f"O{otra}"]], po[0])
            elif otra > linea:
                q[i[f"O{otra}"]] = min(q[i[f"O{otra}"]], po[0])
        for otra in (1.5, 2.5, 3.5):
            q[i[f"U{otra}"]] = 1 - q[i[f"O{otra}"]]
        q[i["U4.5"]] = max(q[i["U4.5"]], q[i["U3.5"]])
    q[i["AH1-1.5"]] = min(q[i["AH1-1.5"]], q[i["1"]])
    q[i["AH2-1.5"]] = min(q[i["AH2-1.5"]], q[i["2"]])
    q[i["AH2+1.5"]], q[i["AH1+1.5"]] = 1 - q[i["AH1-1.5"]], 1 - q[i["AH2-1.5"]]
    return q


def _precios_casa(cuotas):
    """Cuotas reales disponibles por código de mercado."""
    if not cuotas:
        return {}
    c = cuotas.get("cierre") or {}
    out = {"1": c.get("h"), "X": c.get("x"), "2": c.get("a")}
    gl = cuotas.get("goles")
    if gl and f"O{gl['linea']}" in _IDX:
        out[f"O{gl['linea']}"] = gl["over"]
        out[f"U{gl['linea']}"] = gl["under"]
    return {k: v for k, v in out.items() if v}


def recomendar(M, Ms, L, V, cuotas, rho, conf, config_valor=None, excluidos=(), previos=None, mapas=None,
               kappa=KAPPA_INICIAL, n_min=None, liga_val=None):
    """Tabla de mercados, una recomendación por perfil y avisos de riesgo para un partido.

    n_min: partidos efectivos del equipo con menos datos; liga_val: validación de esa liga (rps, rps_base, n).
    """
    previos = previos or {}
    excluidos = excluidos or {}
    dif_mercado = mov_contra = mov_max = 0.0
    incoherente = False
    pk = po = linea = None
    g, d = probs_mercados(M[None])
    gs, ds = probs_mercados(Ms)
    pm = _efectiva(g[0], d[0])
    sims = _efectiva(gs, ds)
    p10m, p90m = np.percentile(sims, 10, axis=0), np.percentile(sims, 90, axis=0)
    p, p10, p90, pg, pdv = pm.copy(), p10m.copy(), p90m.copy(), g[0].copy(), d[0].copy()
    base, ajuste, avisos = "modelo", None, []
    precios = _precios_casa(cuotas)
    if cuotas and all((cuotas.get("cierre") or {}).get(k) for k in ("h", "x", "a")):
        c = cuotas["cierre"]
        pk, margen = desmarginar([c["h"], c["x"], c["a"]])
        p_over = linea = None
        if cuotas.get("goles"):
            po, _ = desmarginar([cuotas["goles"]["over"], cuotas["goles"]["under"]])
            p_over, linea = float(po[0]), cuotas["goles"]["linea"]
        lh, la, err = lambdas_de_mercado(pk, rho, p_over, linea)
        gm, dm = probs_mercados(matriz(np.array([lh]), np.array([la]), rho))
        p = _efectiva(gm[0], dm[0])
        pdv = dm[0]
        base = "mercado"
        ajuste = {"lh": round(lh, 3), "la": round(la, 3), "error": round(err, 4), "margen": round(margen, 4),
                  "proveedor": cuotas.get("proveedor", "")}
        if err > 0.02:
            incoherente = True
            avisos.append("Las cuotas de la casa no encajan bien entre sí (1X2 frente a la línea de goles): "
                          "los mercados derivados son menos fiables.")
        dif_mercado = float(np.max(np.abs(pm[:3] - p[:3])))
        if dif_mercado >= 0.10:
            avisos.append(f"El modelo y la casa difieren {dif_mercado * 100:.0f} puntos: se usan las cuotas, que en "
                          f"las pruebas fueron más precisas (D24.15). Revisa noticias antes de apostar.")
        ap = cuotas.get("apertura")
        if ap and all(ap.values()):
            pa, _ = desmarginar([ap["h"], ap["x"], ap["a"]])
            mov = mov_max = float(np.max(np.abs(pa - pk)))
            mov_contra = pa - pk     # caída de probabilidad de cada resultado 1X2 desde la apertura
            if mov >= 0.05:
                avisos.append(f"Las cuotas se movieron {mov * 100:.0f} puntos desde la apertura: puede haber una "
                              f"noticia (lesión, alineación). Revísala antes de apostar.")
    # mismo orden que en la prueba retrospectiva: cuotas exactas de la casa → recalibración aprendida
    if base == "mercado":
        p = fijar_mercado(p, pk, po, linea)
        pdv = pdv.copy()
        pdv[_IDX["DNB1"]] = pdv[_IDX["DNB2"]] = pk[1]
    mapa = (mapas or {}).get(base)
    if mapa:
        p = recalibrar(p, mapa)
    if base == "mercado":
        # lo que la casa cotiza directamente queda con SU probabilidad exacta: la recalibración no se probó
        # con dinero real contra el precio de la casa, así que no puede crear "valor" frente a ella
        p = fijar_mercado(p, pk, po, linea)
    # la incertidumbre del modelo se traslada como margen alrededor de la probabilidad final
    p10 = np.clip(p - (pm - p10m), 0.001, p)
    p90 = np.clip(p + (p90m - pm), p, 0.999)
    pg = p * (1 - pdv)
    prud = prudente(p, p10, base, kappa)
    ancho = (p90 - p10) if base == "modelo" else np.zeros_like(p)
    justa = 1 / np.maximum(p, 1e-6)
    excl = excluidos.get(base, []) if isinstance(excluidos, dict) else list(excluidos)
    filas = []
    for k, cod in enumerate(_COD):
        texto, grupo = MERCADOS[cod][0], MERCADOS[cod][1]
        cmin = cuota_minima(float(p[k]), float(prud[k]), base)
        f = {"cod": cod, "txt": texto.replace("{L}", L).replace("{V}", V), "grupo": grupo,
             "p": round(float(p[k]), 4), "p10": round(float(p10[k]), 4), "p90": round(float(p90[k]), 4),
             "prud": round(float(prud[k]), 4), "p_gana": round(float(pg[k]), 4), "p_dev": round(float(pdv[k]), 4),
             "p_modelo": round(float(pm[k]), 4), "justa": round(float(justa[k]), 2), "minima": cmin,
             "alerta": cuota_alerta(float(p[k]), cmin)}
        if cod in precios:
            # contra el precio de la misma casa se usa SU probabilidad sin margen: el resultado siempre refleja
            # la comisión que cobra, nunca un "valor" creado por el residuo del ajuste
            o = float(precios[cod])
            f["cuota_casa"] = round(o, 2)
            f["ev_casa"] = round(float(p[k]) * o - 1, 4)
        if cod in excl:
            f["no_calibrado"] = True
        filas.append(f)

    # banderas comunes al partido
    alto_partido, medio_partido = [], []
    if n_min is not None and n_min < 4:
        alto_partido.append("muy pocos partidos recientes de un equipo")
    elif n_min is not None and n_min < 8 or conf == "baja":
        medio_partido.append("pocos partidos recientes de un equipo")
    if base == "modelo" and liga_val:
        if liga_val.get("n", 0) >= 100 and liga_val["rps"] >= liga_val["rps_base"]:
            alto_partido.append("en esta liga el modelo no supera a la referencia ingenua")
        elif liga_val.get("n", 0) < 100 or liga_val["rps"] > 0.98 * liga_val["rps_base"]:
            medio_partido.append("en esta liga el modelo está poco probado")
    if dif_mercado >= 0.15:
        alto_partido.append(f"el modelo y la casa difieren {dif_mercado * 100:.0f} puntos")
    elif dif_mercado >= 0.10:
        medio_partido.append(f"el modelo y la casa difieren {dif_mercado * 100:.0f} puntos")
    if mov_max >= 0.05:
        medio_partido.append(f"las cuotas se movieron {mov_max * 100:.0f} puntos")
    if incoherente:
        medio_partido.append("las cuotas de la casa no encajan bien entre sí")

    perfiles = {}
    for nombre in PERFILES:
        k, respaldo, sin_rango = elegir(p, prud, ancho, nombre, excl, previos.get(nombre))
        f = filas[k]
        alto, medio = list(alto_partido), list(medio_partido)
        if sin_rango:
            alto.append("no hay ninguna opción clara en este partido")
        if ancho[k] > ANCHO_MAX:
            alto.append("probabilidad muy incierta")
        elif ancho[k] > ANCHO_AVISO:
            medio.append("probabilidad algo incierta")
        if isinstance(mov_contra, np.ndarray) and f["cod"] in ("1", "X", "2") and \
                mov_contra[("1", "X", "2").index(f["cod"])] >= 0.03:
            alto.append("la cuota se movió en contra de esta opción")
        if respaldo:
            medio.append("no había opciones en el rango del perfil")
        riesgo = "alto" if alto else "medio" if medio else "bajo"
        perfiles[nombre] = {"cod": f["cod"], "riesgo": riesgo, "respaldo": respaldo, "motivos": alto + medio,
                            "stake_tope": tope_stake(nombre, riesgo)}
    valor = None
    if config_valor and config_valor.get("habilitado"):
        w = config_valor.get("peso_modelo", 0.0)
        mejores = []
        for f in filas:
            if "cuota_casa" not in f:
                continue
            pmix = w * f["p_modelo"] + (1 - w) * f["p"]
            e = pmix * f["cuota_casa"] - 1
            if e >= EV_MIN_VALOR and pmix >= P_MIN_VALOR and f["cuota_casa"] <= CUOTA_MAX_VALOR:
                mejores.append((e, f["cod"], pmix))
        if mejores:
            e, cod, pmix = max(mejores)
            f = filas[_IDX[cod]]
            k = _IDX[cod]
            prud_mix = pmix - kappa * w * (float(pm[k]) - float(p10m[k]))   # monto con la probabilidad prudente
            valor = {"cod": cod, "ev": round(e, 4), "p": round(pmix, 4),
                     "stake": stake_pct(prud_mix, f["p_dev"], f["cuota_casa"], "equilibrado", "medio")}
    if base == "modelo":
        avisos.append("ESPN no tiene cuotas de este partido: compara la cuota mínima con la de tu casa.")
    return {"base": base, "ajuste": ajuste, "mercados": filas, "perfiles": perfiles, "valor": valor,
            "avisos": avisos}


# ---------------------------------------------------------------- prueba retrospectiva con cuotas reales

def _cargar_lambdas():
    try:
        return json.loads(ARCHIVO_LAMBDAS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def calibrar_peso(filas_val, cuotas_hist, rejilla=np.round(np.arange(0, 1.0001, 0.05), 2)):
    """Peso del modelo frente al mercado que minimiza el log-loss del 1X2 con cuotas de cierre reales."""
    pm, pk, pp, y = [], [], [], []
    for f in filas_val:
        c = cuotas_hist.get(f[9]["id"])
        if not c:
            continue
        pm.append([f[1], f[2], f[3]])
        pk.append(desmarginar(c["x12"], "proporcional")[0])
        pp.append(desmarginar(c["x12"], "potencia")[0])
        y.append(0 if f[4] > f[5] else 1 if f[4] == f[5] else 2)
    if len(y) < 300:
        return None
    pm, pk, pp, y = np.array(pm), np.array(pk), np.array(pp), np.array(y)
    filas_idx = np.arange(len(y))

    def ll(p):
        return float(-np.log(np.clip(p[filas_idx, y], 1e-9, 1)).mean())

    # método para quitar el margen: se cambia a "potencia" solo si mejora el log-loss en 0,001 o más
    ll_prop, ll_pot = ll(pk), ll(pp)
    metodo = "potencia" if ll_prop - ll_pot >= 0.001 else "proporcional"
    if metodo == "potencia":
        pk = pp
    res = [(ll(w * pm + (1 - w) * pk), float(w)) for w in rejilla]
    mejor_ll, mejor_w = min(res)
    return {"peso_modelo": mejor_w, "n": int(len(y)), "logloss_mezcla": round(mejor_ll, 4),
            "logloss_modelo": round(ll(pm), 4), "logloss_mercado": round(ll(pk), 4),
            "metodo": metodo, "logloss_proporcional": round(ll_prop, 5), "logloss_potencia": round(ll_pot, 5)}


def _roi(ganancias, rng):
    r = np.array(ganancias, dtype=float)
    if len(r) == 0:
        return None
    boots = np.array([rng.choice(r, len(r)).mean() for _ in range(1000)])
    return {"n": int(len(r)), "roi": round(float(r.mean()), 4),
            "ic": [round(float(np.percentile(boots, 10)), 4), round(float(np.percentile(boots, 90)), 4)],
            "unidades": round(float(r.sum()), 1)}


def backtest(filas_val, cuotas_hist, sims=120, semilla=7):
    """Cómo habrían salido las recomendaciones en los partidos de la prueba retrospectiva.

    Tramos cronológicos (las filas vienen semana a semana): 0-40% aprende la recalibración; 40-70% decide la
    puerta de calibración y κ; 70-100% mide el acierto y la ganancia que se muestran (fuera de muestra).
    """
    rng = np.random.default_rng(semilla)
    cache, usadas = _cargar_lambdas(), {}
    n_total = len(filas_val)
    corte1, corte2 = int(n_total * 0.4), int(n_total * 0.7)
    # peso del modelo y método del margen: solo con partidos anteriores al tramo de evaluación
    peso = calibrar_peso(filas_val[:corte2], cuotas_hist)
    w = peso["peso_modelo"] if peso else 0.0
    metodo = peso["metodo"] if peso else "proporcional"
    METODO_DESMARGINADO["metodo"] = metodo
    valor_g, fav_g = [], []
    guardado = []          # (p, p10, p90, resultado por mercado, precios, base) de cada partido
    # lotes de hasta 300 filas con el mismo rho (cada semana de la prueba tiene su propio ajuste)
    lotes, actual = [], []
    for f in filas_val:
        if actual and (f[9]["rho"] != actual[0][9]["rho"] or len(actual) >= 300):
            lotes.append(actual)
            actual = []
        actual.append(f)
    if actual:
        lotes.append(actual)
    pos = 0
    for lote in lotes:
        n = len(lote)
        lh = np.array([f[9]["lh"] for f in lote])
        la = np.array([f[9]["la"] for f in lote])
        sdh = np.minimum(np.nan_to_num(np.array([f[9]["sdh"] for f in lote]), nan=0.7), 1.0)
        sda = np.minimum(np.nan_to_num(np.array([f[9]["sda"] for f in lote]), nan=0.7), 1.0)
        rho = lote[0][9]["rho"]
        z = rng.standard_normal((n, sims, 2))
        Lh = (lh[:, None] * np.exp(z[:, :, 0] * sdh[:, None])).ravel()
        La = (la[:, None] * np.exp(z[:, :, 1] * sda[:, None])).ravel()
        gs, ds = probs_mercados(matriz(Lh, La, rho))
        sim_p = _efectiva(gs, ds).reshape(n, sims, -1)
        g0, d0 = probs_mercados(matriz(lh, la, rho))
        pm = _efectiva(g0, d0)
        p10m = np.percentile(sim_p, 10, axis=1)
        p90m = np.percentile(sim_p, 90, axis=1)
        for j, f in enumerate(lote):
            info = f[9]
            yh, ya = int(f[4]), int(f[5])
            p, p10, p90, pg, pdv = pm[j].copy(), p10m[j].copy(), p90m[j].copy(), g0[j], d0[j]
            c = cuotas_hist.get(info["id"])
            precios = {}
            if c:
                pk = desmarginar(c["x12"], metodo)[0]
                po = desmarginar(c["ou25"], metodo)[0] if c.get("ou25") else None
                clave = f"{info['id']}|{rho}|{metodo}|{c['x12']}|{c.get('ou25')}"
                if clave not in cache:
                    cache[clave] = list(lambdas_de_mercado(pk, rho, po[0] if po is not None else None,
                                                           2.5 if po is not None else None)[:2])
                usadas[clave] = cache[clave]
                mlh, mla = cache[clave]
                gm, dm = probs_mercados(matriz(np.array([mlh]), np.array([mla]), rho))
                p_new = fijar_mercado(_efectiva(gm[0], dm[0]), pk, po, 2.5)
                p10 = np.clip(p_new - (pm[j] - p10m[j]), 0.001, p_new)
                p90 = np.clip(p_new + (p90m[j] - pm[j]), p_new, 0.999)
                p = p_new
                precios = {"1": c["x12"][0], "X": c["x12"][1], "2": c["x12"][2]}
                if c.get("ou25"):
                    precios["O2.5"], precios["U2.5"] = c["ou25"]
                if pos + j >= corte2:
                    # estrategia "valor" (para medir; está apagada en la app mientras pierda)
                    pmix = w * np.array([f[1], f[2], f[3]]) + (1 - w) * pk
                    real = 0 if yh > ya else 1 if yh == ya else 2
                    for k in range(3):
                        o = c["x12"][k]
                        if pmix[k] * o - 1 >= EV_MIN_VALOR and pmix[k] >= P_MIN_VALOR and o <= CUOTA_MAX_VALOR:
                            valor_g.append(o - 1 if k == real else -1.0)
                    kf = int(np.argmax([f[1], f[2], f[3]]))
                    fav_g.append(c["x12"][kf] - 1 if kf == real else -1.0)
            res = [liquidar(cod, yh, ya) for cod in _COD]
            fijo = (pk, po) if c else None
            guardado.append((p, p10, p90, res, precios, "mercado" if c else "modelo", fijo))
        pos += n
    try:
        ARCHIVO_LAMBDAS.write_text(json.dumps(usadas), encoding="utf-8")   # solo lo que sigue en uso
    except OSError:
        pass

    aprender, puerta, evaluacion = guardado[:corte1], guardado[corte1:corte2], guardado[corte2:]

    def mapas_de(filas):
        out = {}
        for base in ("modelo", "mercado"):
            fs = [g for g in filas if g[5] == base]
            if len(fs) < 500:
                continue
            out[base] = {}
            for cod in PRIMARIOS:
                k = _IDX[cod]
                ps = np.array([g[0][k] for g in fs])
                rs = np.array([g[3][k] for g in fs])
                m = rs != "devuelve"
                out[base][cod] = ajustar_mapa(ps[m], (rs[m] == "gana").astype(float))
        return out

    def calibrada(g, mapas):
        mp = mapas.get(g[5])
        if not mp:
            return g
        q = recalibrar(g[0], mp)
        if g[6] is not None:                     # igual que en vivo: lo cotizado queda con la cuota exacta
            q = fijar_mercado(q, g[6][0], g[6][1], 2.5)
        d = q - g[0]
        return (q, np.clip(g[1] + d, 0.001, q), np.clip(g[2] + d, q, 0.999), g[3], g[4], g[5], g[6])

    def ece(filas, k):
        ps = np.array([g[0][k] for g in filas])
        rs = np.array([g[3][k] for g in filas])
        m = rs != "devuelve"
        ps, ok = ps[m], (rs[m] == "gana").astype(float)
        if len(ps) < 200:
            return None, len(ps), None, None
        e = 0.0
        for lo in np.arange(0, 1, 0.1):
            t = (ps >= lo) & (ps < lo + 0.1)
            if t.any():
                e += t.mean() * abs(ps[t].mean() - ok[t].mean())
        return float(e), int(len(ps)), float(ps.mean()), float(ok.mean())

    mapas_prueba = mapas_de(aprender)
    puerta_cal = [calibrada(g, mapas_prueba) for g in puerta]
    eval_cal = [calibrada(g, mapas_prueba) for g in evaluacion]

    # puerta de calibración, por separado para partidos con y sin cuotas (en vivo se aplica igual)
    calib, excluidos = {}, {}
    for base in ("modelo", "mercado"):
        crudas = [g for g in puerta if g[5] == base]
        cal = [g for g in puerta_cal if g[5] == base]
        calib[base], excluidos[base] = {}, []
        for k, cod in enumerate(_COD):
            e0, _, _, _ = ece(crudas, k)
            e1, n, esp, real = ece(cal, k)
            if e1 is None:                       # sin datos suficientes, la puerta queda cerrada
                if cod in CANDIDATOS:
                    excluidos[base].append(cod)
                continue
            sesgo = real - esp      # negativo = gana menos de lo que promete (lo peligroso para quien apuesta)
            calib[base][cod] = {"n": n, "ece_sin": round(e0, 4), "ece": round(e1, 4), "esperado": round(esp, 4),
                                "real": round(real, 4), "sesgo": round(sesgo, 4)}
            if cod in CANDIDATOS and (n < N_MIN_PUERTA or e1 > ECE_MAX or sesgo < -SESGO_MAX):
                excluidos[base].append(cod)

    # κ = 0,5 solo si en los partidos SIN cuotas (los únicos donde κ cambia algo) cada perfil acierta al menos
    # lo prometido − 0,5 puntos, con 1000 partidos o más; se decide en el tramo de la puerta, no en el de evaluación
    sin_cuotas = [g for g in puerta_cal if g[5] == "modelo"]
    prueba_k = {nombre: _evaluar_perfil(nombre, KAPPA_VALIDADO, sin_cuotas, excluidos, rng) for nombre in PERFILES}
    kappa = KAPPA_VALIDADO if len(sin_cuotas) >= 1000 and all(
        v["acierto"] >= v["esperado"] - 0.005 for v in prueba_k.values()) else KAPPA_INICIAL
    perfiles = {nombre: _evaluar_perfil(nombre, kappa, eval_cal, excluidos, rng) for nombre in PERFILES}
    mapas_finales = mapas_de(guardado)
    v = _roi(valor_g, rng)
    habilitado = bool(v and v["n"] >= 300 and v["ic"][0] > 0)
    return {"peso": peso, "perfiles": perfiles, "valor": v, "favorito_modelo": _roi(fav_g, rng),
            "valor_habilitado": habilitado, "peso_modelo": w, "calibracion_mercados": calib,
            "excluidos": excluidos, "mapas": mapas_finales, "n_verificacion": len(eval_cal),
            "n_puerta": len(puerta_cal), "kappa": kappa, "metodo": metodo,
            "kappa_prueba": {k: {"acierto": x["acierto"], "esperado": x["esperado"], "n": x["n"]}
                             for k, x in prueba_k.items()}}


def _evaluar_perfil(nombre, kappa, verificados, excluidos, rng):
    """Acierto real frente al prometido, y ganancia con la cuota real de cierre cuando existe."""
    acc = {"gana": 0, "pierde": 0, "devuelve": 0, "p": [], "ganancias": [], "mercados": {}}
    for p, p10, p90, res, precios, base, _fijo in verificados:
        prud = prudente(p, p10, base, kappa)
        ancho = (p90 - p10) if base == "modelo" else np.zeros_like(p)
        k, _, _ = elegir(p, prud, ancho, nombre, excluidos.get(base, []))
        cod, r = _COD[k], res[k]
        acc[r] += 1
        acc["p"].append(float(p[k]))
        acc["mercados"][cod] = acc["mercados"].get(cod, 0) + 1
        if cod in precios:
            acc["ganancias"].append(precios[cod] - 1 if r == "gana" else (0.0 if r == "devuelve" else -1.0))
    dec = acc["gana"] + acc["pierde"]
    top = sorted(acc["mercados"].items(), key=lambda x: -x[1])[:6]
    total = dec + acc["devuelve"]
    return {"n": total, "acierto": round(acc["gana"] / max(dec, 1), 4),
            "esperado": round(float(np.mean(acc["p"])) if acc["p"] else 0, 4),
            "devueltas": acc["devuelve"], "con_cuota_real": _roi(acc["ganancias"], rng),
            "fraccion_con_cuota": round(len(acc["ganancias"]) / max(total, 1), 4),
            "mercados_mas_usados": [{"cod": c, "n": n} for c, n in top], "kappa": kappa}
