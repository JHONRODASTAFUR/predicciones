"""Motor de recomendaciones inteligentes.

Qué hace, en simple (por cada partido que aún no empieza):
  1. CLASIFICA cada apuesta: Conservadora, Moderada, Riesgo elevado o Sin valor estadístico.
  2. Da un VEREDICTO: si ninguna es sólida dice «NO HAY APUESTA RECOMENDADA» (no fuerza pronósticos).
  3. EXPLICA: factores a favor, factores de riesgo y condiciones que invalidarían la recomendación.
  4. DETECTA DISCREPANCIAS: cuando modelo, cuotas, racha, alineación o noticias cuentan historias distintas.
  5. SIGUE LAS CUOTAS: guarda una foto en cada actualización y mide cuánto se movieron.
  6. ARMA ESCENARIOS: qué pasa si falta el goleador, el portero o si el equipo rota titulares.
  7. MIDE LA CARGA: partidos recientes de cada equipo y kilómetros de viaje del visitante.

Reglas:
  · Nada de esto cambia las probabilidades del modelo: solo las interpreta y avisa.
  · Cada frase sale de un dato real del partido; si el dato no existe, la frase no aparece (no se inventa).
  · La clase mide riesgo e incertidumbre, nunca promete ganancias.
"""
import datetime as dt
import math
from collections import Counter

from . import geo

NOMBRE_CLASE = {"conservadora": "Conservadora", "moderada": "Moderada", "elevado": "Riesgo elevado",
                "sin_valor": "Sin valor estadístico"}
BONO_CLASE = {"conservadora": 30, "moderada": 15, "elevado": 0, "sin_valor": -100}
MAX_FOTOS = 40                 # fotos de cuotas que se guardan por partido
MOVIMIENTO_AVISO = 0.03        # 3 puntos de probabilidad
DIF_MODELO_MERCADO = 0.08


def _n(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _pc(p):
    return f"{round(p * 100)} %"


def _t(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ------------------------------------------------------------------ clasificación

def clase_de(f, x):
    """Clase de riesgo de una apuesta. f = fila del mercado; x = datos del perfil (riesgo de los datos y motivos)."""
    if f.get("no_calibrado"):
        return "sin_valor", "este mercado no pasó la prueba de calibración con partidos pasados"
    if x.get("riesgo") == "alto":
        return "sin_valor", (x.get("motivos") or ["los datos de este partido son poco fiables"])[0]
    p, piso = f["p"], f.get("p10", f["p"])
    if p >= 0.70 and piso >= 0.60 and x.get("riesgo") == "bajo":
        return "conservadora", f"acierta cerca de {round(p * 10)} de cada 10 y su estimación más baja sigue sobre 60 %"
    if p >= 0.50 and piso >= 0.40:
        return "moderada", f"acierta cerca de {round(p * 10)} de cada 10, con incertidumbre aceptable"
    return "elevado", "falla tanto o más de lo que acierta, o su probabilidad es muy incierta"


def indice_evidencia(item, f, x):
    """Índice 0-100 de la CALIDAD DE LOS DATOS que respaldan la apuesta (no es probabilidad de acertar)."""
    pts = 50
    if (item.get("apuesta") or {}).get("base") == "mercado":
        pts += 15                                    # hay cuotas reales: en las pruebas predicen mejor que el modelo
    pts += {"alta": 15, "media": 5, "baja": -15}.get(item["pred"].get("conf"), 0)
    if (item.get("alineacion") or {}).get("estado") == "confirmada":
        pts += 10
    if f.get("no_calibrado"):
        pts -= 30
    pts -= {"bajo": 0, "medio": 15, "alto": 35}.get(x.get("riesgo"), 15)
    pts -= min(15, round((f.get("p90", f["p"]) - f.get("p10", f["p"])) * 50))
    return max(0, min(100, pts))


def orientacion(cod):
    """¿A qué equipo y a qué sentido de goles apunta un mercado? → (lado, goles)."""
    lado = None
    if cod in ("1", "1X", "DNB1", "CS_L", "WTN_L") or cod.startswith(("AH1", "L_O", "M1_", "1&")):
        lado = "h"
    elif cod in ("2", "X2", "DNB2", "CS_V", "WTN_V") or cod.startswith(("AH2", "V_O", "M2_", "2&")):
        lado = "a"
    if cod.startswith("O") or cod in ("BTTS_SI", "T4+") or "&O" in cod or "&SI" in cod:
        goles = "mas"
    elif cod.startswith("U") or cod in ("BTTS_NO", "T0", "T1") or "&U" in cod or "&NO" in cod:
        goles = "menos"
    else:
        goles = None
    return lado, goles


# ------------------------------------------------------------------ explicación

def a_favor_lado(item, s):
    """Datos del partido que respaldan al equipo del lado s."""
    o = "a" if s == "h" else "h"
    ctx, al, pred = item.get("ctx") or {}, item.get("alineacion") or {}, item["pred"]
    eq, rival, out = item[s]["n"], item[o]["n"], []
    lam, lam_o = (pred["lh"], pred["la"]) if s == "h" else (pred["la"], pred["lh"])
    if lam - lam_o >= 0.3:
        out.append(f"goles esperados {_n(lam)} contra {_n(lam_o)}")
    mer = item.get("mercado")
    if mer and mer["pH" if s == "h" else "pA"] >= 0.5:
        out.append(f"las cuotas también lo ven favorito ({_pc(mer['pH' if s == 'h' else 'pA'])} sin el margen de la casa)")
    donde, donde_o = ("local", "visita") if s == "h" else ("visita", "local")
    casa = al.get("casa") or {}
    c, co = (casa.get(s) or {}).get(donde), (casa.get(o) or {}).get(donde_o)
    if c and c.get("n", 0) >= 8 and c["puntos"] >= 1.8:
        out.append(f"{eq} de {donde}: {_n(c['puntos'])} puntos por partido en {c['n']} partidos")
    if co and co.get("n", 0) >= 8 and co["puntos"] <= 1.0:
        out.append(f"{rival} de {donde_o}: solo {_n(co['puntos'])} puntos por partido en {co['n']} partidos")
    t, to = (ctx.get(s) or {}).get("tabla"), (ctx.get(o) or {}).get("tabla")
    if t and to and t.get("grupo") == to.get("grupo") and to["pos"] - t["pos"] >= 5:
        out.append(f"{eq} está {to['pos'] - t['pos']} puestos por encima en la tabla")
    vs = (al.get(s) or {}).get("vs_similares")
    if vs and vs.get("n", 0) >= 8 and vs.get("dif_ataque", 0) >= 0.10:
        out.append(f"{eq} rinde sobre lo esperado ante rivales de estilo «{al[s].get('estilo_rival')}» "
                   f"({vs['g']}-{vs['e']}-{vs['p']} en {vs['n']})")
    d, do = (ctx.get(s) or {}).get("descanso"), (ctx.get(o) or {}).get("descanso")
    if d is not None and do is not None and do <= 4 and d - do >= 2:
        out.append(f"{eq} llega con {_n(d - do, 0)} días más de descanso")
    f = (ctx.get(s) or {}).get("forma") or ""
    if len(f) >= 4 and f.count("G") >= 3:
        out.append(f"{eq} ganó {f.count('G')} de sus últimos {len(f)} (muestra corta)")
    return out


def a_favor_goles(item, sentido, cod):
    """Datos que respaldan un mercado de muchos o de pocos goles."""
    pred, ctx, out = item["pred"], item.get("ctx") or {}, []
    tot = pred["lh"] + pred["la"]
    ult = [r["gf"] + r["gc"] for s in ("h", "a") for r in ((ctx.get(s) or {}).get("ultimos") or [])]
    if cod == "BTTS_SI" and min(pred["lh"], pred["la"]) >= 1.0:
        out.append(f"los dos equipos superan 1 gol esperado ({_n(pred['lh'])} y {_n(pred['la'])})")
    if sentido == "mas":
        if tot >= 2.6:
            out.append(f"se esperan {_n(tot)} goles en total")
        k = sum(1 for g in ult if g >= 3)
        if len(ult) >= 8 and k / len(ult) >= 0.6:
            out.append(f"{k} de los últimos {len(ult)} partidos de ambos tuvieron 3 goles o más (muestra corta)")
    else:
        if tot <= 2.4:
            out.append(f"se esperan solo {_n(tot)} goles en total")
        k = sum(1 for g in ult if g <= 2)
        if len(ult) >= 8 and k / len(ult) >= 0.6:
            out.append(f"{k} de los últimos {len(ult)} partidos de ambos tuvieron 2 goles o menos (muestra corta)")
    return out


def jugadores_clave(al_lado, n=3):
    """Los jugadores que más pueden mover el resultado: aporte en ataque y defensa + probabilidad de gol."""
    def peso(j):
        return abs(j.get("ataque") or 0) + abs(j.get("defensa") or 0) + 0.5 * (j.get("prob_gol") or 0)
    js = sorted(al_lado.get("jugadores") or [], key=peso, reverse=True)[:n]
    return [{"id": j["id"], "n": j["n"], "linea": j.get("linea"), "influencia": round(peso(j), 3)} for j in js]


def invalidan(item, f, lado, claves):
    """Condiciones que, si ocurren, dejan sin efecto la recomendación."""
    out = []
    if f.get("minima"):
        out.append(f"Betano paga menos de {_n(f['minima'])}")
    if f.get("alerta"):
        out.append(f"la cuota supera {_n(f['alerta'])}: suele indicar una noticia que el modelo no conoce")
    al = item.get("alineacion")
    if al:
        lados = [lado] if lado else ["h", "a"]
        for s in lados:
            bajas = [j["n"] for j in al[s].get("jugadores") or [] if j.get("posible_baja")]
            if bajas:
                out.append(f"se confirma la baja de {', '.join(bajas[:2])} ({item[s]['n']})")
            elif al.get("estado") == "probable" and lado and claves.get(s):
                out.append(f"el once confirmado de {item[s]['n']} sale sin {' ni '.join(c['n'] for c in claves[s][:2])}")
    return out[:4]


def riesgos_partido(item, carga):
    """Riesgos que afectan a cualquier apuesta del partido."""
    out = [s["texto"] for s in item.get("senales") or [] if s.get("nivel") in ("amarillo", "rojo")]
    al = item.get("alineacion")
    if al and al.get("estado") == "probable":
        out.append("alineación sin confirmar: el once mostrado es el habitual (estimación)")
    elif not al:
        out.append("sin datos de alineación para este partido")
    for s in ("h", "a"):
        if (carga.get(s) or {}).get("p8", 0) >= 3:
            out.append(f"{item[s]['n']} jugó {carga[s]['p8']} partidos en los últimos 8 días (posible fatiga)")
    if carga.get("viaje_km", 0) >= 2500:
        out.append(f"{item['a']['n']} viaja cerca de {carga['viaje_km']:,} km".replace(",", " "))
    return out[:7]


# ------------------------------------------------------------------ discrepancias

def discrepancias(item):
    """Señales que se contradicen entre sí: conviene mirarlas antes de apostar."""
    out, pred, mer, al, ctx = [], item["pred"], item.get("mercado"), item.get("alineacion"), item.get("ctx") or {}
    pm = pred.get("solo_modelo") or pred
    nombres = {"pH": item["h"]["n"], "pD": "el empate", "pA": item["a"]["n"]}
    if mer:
        k = max(("pH", "pD", "pA"), key=lambda q: abs(pm[q] - mer[q]))
        d = pm[k] - mer[k]
        if abs(d) >= DIF_MODELO_MERCADO:
            out.append({"tipo": "modelo-mercado", "nivel": "rojo" if abs(d) >= 0.15 else "amarillo",
                        "texto": f"El modelo solo da {_pc(pm[k])} a {nombres[k]} y las cuotas {_pc(mer[k])} "
                                 f"({d * 100:+.0f} puntos). Suele tener razón el mercado."})
        mv = mer.get("movimiento")
        if mv and mv["max"] >= MOVIMIENTO_AVISO:
            q, dq = ("pH", mv["dH"]) if abs(mv["dH"]) >= abs(mv["dA"]) else ("pA", mv["dA"])
            se_aleja = (pm[q] - mer[q]) * dq < 0
            out.append({"tipo": "cuotas", "nivel": "amarillo",
                        "texto": f"Las cuotas de {nombres[q]} se movieron {'+' if dq >= 0 else '-'}"
                                 f"{_n(abs(dq) * 100, 1)} puntos"
                                 + (": se alejan de lo que opina el modelo." if se_aleja
                                    else ": van hacia lo que opina el modelo.")})
    fh, fa = (ctx.get("h") or {}).get("forma") or "", (ctx.get("a") or {}).get("forma") or ""
    if len(fh) >= 4 and len(fa) >= 4 and max(pred["pH"], pred["pA"]) >= 0.45:
        pts = lambda f: 3 * f.count("G") + f.count("E")      # noqa: E731
        fav, otro, ff, fo = ("h", "a", fh, fa) if pred["pH"] >= pred["pA"] else ("a", "h", fa, fh)
        if pts(fo) - pts(ff) >= 6:
            out.append({"tipo": "racha", "nivel": "info",
                        "texto": f"{item[fav]['n']} es favorito pero llega con peor racha que {item[otro]['n']} "
                                 f"({ff} contra {fo}). Cinco partidos son una muestra corta."})
    if al:
        for s in ("h", "a"):
            sale = al[s].get("sale") or []
            if al.get("estado") == "confirmada" and len(sale) >= 3:
                out.append({"tipo": "alineacion", "nivel": "amarillo",
                            "texto": f"{item[s]['n']} sale con {len(sale)} cambios frente a su once habitual."})
            bajas = [j["n"] for j in al[s].get("jugadores") or [] if j.get("posible_baja")]
            if bajas and al.get("estado") == "probable":
                out.append({"tipo": "noticias", "nivel": "amarillo",
                            "texto": f"El once probable de {item[s]['n']} incluye a {', '.join(bajas[:3])}, que las "
                                     f"noticias dan como duda o baja (titulares sin verificar)."})
    return out


# ------------------------------------------------------------------ carga de partidos y viaje

def _km(a, b):
    r = math.pi / 180
    h = (math.sin((b["lat"] - a["lat"]) * r / 2) ** 2
         + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin((b["lon"] - a["lon"]) * r / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def _sede(equipo, por_equipo, cache):
    """Ciudad donde el equipo juega habitualmente de local (la más repetida, mínimo 3 partidos)."""
    if equipo not in cache:
        c = Counter(geo.clave(x.get("ciudad"), x.get("pais")) for x in por_equipo.get(equipo, ())
                    if x["h"]["id"] == equipo and not x.get("neutral") and x.get("ciudad"))
        mejor = c.most_common(1)
        cache[equipo] = mejor[0][0] if mejor and mejor[0][1] >= 3 else None
    return cache[equipo]


def carga_y_viaje(e, env, cache):
    """Partidos jugados por cada equipo en los últimos 8 y 14 días, y kilómetros de viaje del visitante."""
    por_equipo, lugares = env.get("por_equipo") or {}, env.get("lugares") or {}
    t0, out = _t(e["fecha"]), {}
    lim = {d: (t0 - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%MZ") for d in (8, 14)}
    for s in ("h", "a"):
        previos = [x["fecha"] for x in por_equipo.get(e[s]["id"], ()) if x.get("fin") and x["fecha"] < e["fecha"]]
        out[s] = {"p8": sum(1 for f in previos if f >= lim[8]), "p14": sum(1 for f in previos if f >= lim[14])}
    if not e.get("neutral"):
        sede = _sede(e["a"]["id"], por_equipo, cache)
        aqui, alla = lugares.get(geo.clave(e.get("ciudad"), e.get("pais"))), lugares.get(sede) if sede else None
        if aqui and alla and aqui.get("lat") is not None and alla.get("lat") is not None:
            out["viaje_km"] = round(_km(alla, aqui))
    return out


# ------------------------------------------------------------------ escenarios «¿qué pasa si…?»

def escenarios(item, e, env, tope):
    """Goles esperados si falta el goleador, el portero o si el equipo rota 3 titulares.

    Usa el mismo cálculo validado del ajuste por alineación (aporte de cada jugador × β, con tope). Solo para
    alineaciones probables: con el once confirmado ya se sabe quién juega."""
    al, jug, pred = item.get("alineacion"), env.get("jug"), item["pred"]
    if not al or al.get("estado") != "probable" or not jug:
        return []
    J, beta, out = jug["J"], jug["cfg"].get("beta", 0.0), []
    if not beta:
        return []
    for s in ("h", "a"):
        js = al[s].get("jugadores") or []
        if len(js) < 11:
            continue
        campo = [j for j in js if j.get("linea") != "POR"]
        goleador = max(campo, key=lambda j: j.get("prob_gol") or 0, default=None)
        portero = next((j for j in js if j.get("linea") == "POR"), None)
        rotan = sorted(campo, key=lambda j: -(j.get("minutos") or 0))[:3]
        casos = (("gol", f"{item[s]['n']} sin su goleador", [goleador] if goleador and goleador.get("prob_gol") else []),
                 ("por", f"{item[s]['n']} sin su portero titular", [portero] if portero else []),
                 ("rot", f"{item[s]['n']} rota 3 titulares", rotan))
        for cod, txt, fuera in casos:
            ids = {j["id"] for j in fuera}
            if not ids:
                continue
            once, reemp = J.once_sin(e[s]["id"], ids)
            da, dd = J.delta(e[s]["id"], once)
            if not reemp or (abs(da) < 1e-9 and abs(dd) < 1e-9):
                continue                                  # sin suplentes conocidos no hay con qué estimarlo
            propio = math.exp(max(-tope, min(tope, beta * da)))      # sus goles
            rival = math.exp(max(-tope, min(tope, beta * dd)))       # los goles que recibe
            fh, fa = (propio, rival) if s == "h" else (rival, propio)
            out.append({"cod": f"{cod}_{s}", "txt": txt, "fuera": [j["n"] for j in fuera],
                        "entran": [J.info.get(j, {}).get("n") for j in reemp],
                        "lh": round(pred["lh"] * fh, 3), "la": round(pred["la"] * fa, 3)})
    return out


# ------------------------------------------------------------------ movimiento de cuotas

def _sin_margen(c):
    inv = [1 / x for x in c]
    return [x / sum(inv) for x in inv]


def movimiento(mov, e, mer, ahora_iso):
    """Guarda una foto de las cuotas (si cambiaron) y resume el movimiento desde la primera foto."""
    if mov is None or not mer or not mer.get("cuotas"):
        return None
    c = mer["cuotas"]
    if not all(isinstance(c.get(k), (int, float)) and c[k] > 1 for k in ("h", "x", "a")):
        return None
    serie = mov.setdefault(e["id"], {"fecha": e["fecha"], "fotos": []})
    serie["fecha"] = e["fecha"]
    foto, fotos = [ahora_iso, c["h"], c["x"], c["a"]], serie["fotos"]
    if not fotos or fotos[-1][1:] != foto[1:]:
        fotos.append(foto)
    del fotos[:-MAX_FOTOS]
    if len(fotos) < 2:
        return None
    p0, p1 = _sin_margen(fotos[0][1:]), _sin_margen(fotos[-1][1:])
    d = [p1[i] - p0[i] for i in range(3)]
    return {"desde": fotos[0][0], "fotos": len(fotos), "antes": fotos[0][1:], "dH": round(d[0], 4),
            "dD": round(d[1], 4), "dA": round(d[2], 4), "max": round(max(abs(x) for x in d), 4)}


def limpiar_movimientos(mov, ahora=None):
    """Borra las fotos de partidos que terminaron hace más de 2 días."""
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    for k in [k for k, v in mov.items() if (ahora - _t(v["fecha"])).days >= 2]:
        del mov[k]
    return mov


# ------------------------------------------------------------------ todo junto

def analizar(item, e, env, tope=0.25):
    """Añade item["intel"]: clases, veredicto, explicación, discrepancias, escenarios y carga."""
    al, ap = item.get("alineacion"), item.get("apuesta")
    cache = env.setdefault("cache_intel", {})
    claves = {s: jugadores_clave(al[s]) for s in ("h", "a")} if al else {}
    carga = carga_y_viaje(e, env, cache)
    perfiles = {}
    if ap:
        por_cod = {f["cod"]: f for f in ap["mercados"]}
        for nombre, x in ap["perfiles"].items():
            f = por_cod.get(x["cod"])
            if not f:
                continue
            clase, motivo = clase_de(f, x)
            lado, goles = orientacion(f["cod"])
            favor = (a_favor_lado(item, lado) if lado else []) + (a_favor_goles(item, goles, f["cod"]) if goles else [])
            perfiles[nombre] = {"cod": f["cod"], "clase": clase, "motivo": motivo,
                                "evidencia": indice_evidencia(item, f, x), "favorables": favor[:4],
                                "riesgos": list(x.get("motivos") or [])[:4],
                                "invalidan": invalidan(item, f, lado, claves)}
    clases = [x["clase"] for x in perfiles.values()]
    if not perfiles:
        veredicto = {"cod": "no", "txt": "NO HAY APUESTA RECOMENDADA",
                     "motivo": "no hay datos suficientes para recomendar en este partido"}
    elif all(c == "sin_valor" for c in clases):
        veredicto = {"cod": "no", "txt": "NO HAY APUESTA RECOMENDADA",
                     "motivo": next(iter(perfiles.values()))["motivo"]}
    else:
        veredicto = {"cod": "condicional", "txt": "Solo si Betano paga la cuota mínima o más",
                     "motivo": "con una cuota menor a la mínima el valor esperado es negativo"}
    item["intel"] = {
        "veredicto": veredicto, "perfiles": perfiles, "claves": claves, "carga": carga,
        "riesgos": riesgos_partido(item, carga), "discrepancias": discrepancias(item),
        "escenarios": escenarios(item, e, env, tope),
        # para ordenar las «mejores oportunidades»: calidad de la evidencia + clase (no es ganancia esperada)
        "puntaje": max((x["evidencia"] + BONO_CLASE[x["clase"]] for x in perfiles.values()), default=-100),
    }
    return item["intel"]
