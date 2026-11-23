"""Necesidad de los equipos en la tabla: qué se juega cada uno y cuánto pesa este partido.

Cómo funciona, en simple:
  1. Toma la tabla de la liga (ESPN) y sus zonas: Champions, Europa League, descenso, ascenso…
  2. En las ligas de ida y vuelta arma los partidos que faltan (todos los cruces local-visita que aún no se
     jugaron) y SIMULA 4 000 veces lo que resta de la temporada con las fuerzas del modelo.
  3. De ahí sale la probabilidad de cada objetivo (título, copas, salvarse) y cuánto cambia si el equipo gana,
     empata o pierde ESTE partido. Con eso se dice si «necesita ganar» o si «le sirve el empate».
  4. Donde no se puede simular (ligas con otro formato, copas) solo se describe: puesto, puntos y distancia
     a cada zona. En eliminatorias de ida y vuelta se indica el marcador global.

Regla esencial: necesitar ganar NO significa tener más probabilidad de ganar. Esto es contexto: no cambia las
probabilidades del modelo. Las plazas por zona son las que publica ESPN (pueden variar por campeones de copa) y
los empates en puntos se resuelven por diferencia de goles (aproximación: algunas ligas usan el duelo directo).
"""
import zlib

import numpy as np

SIMULACIONES = 4000
# ligas de todos contra todos a ida y vuelta, sin fases ni playoffs que alteren la tabla
IDA_Y_VUELTA = {"esp.1", "esp.2", "eng.1", "eng.2", "eng.3", "eng.4", "ita.1", "ita.2", "ger.1", "ger.2",
                "fra.1", "fra.2", "por.1", "ned.1", "tur.1", "bra.1"}
ZONAS_ES = {
    "champions league": "Champions League", "champions league qualifying": "previa de Champions League",
    "europa league": "Europa League", "europa league qualifying": "previa de Europa League",
    "conference league": "Conference League", "conference league qualifying": "previa de Conference League",
    "relegation": "descenso", "relegated": "descenso", "relegation playoff": "promoción de descenso",
    "relegation playoffs": "promoción de descenso", "promotion": "ascenso", "promoted": "ascenso",
    "promotion playoff": "playoff de ascenso", "promotion playoffs": "playoff de ascenso", "playoffs": "playoffs",
}
CASI = 0.97          # desde aquí un objetivo está «prácticamente asegurado»; por debajo de 1−CASI, «sin opciones»
# peso del partido = cuánto cambia la probabilidad del objetivo entre ganar y perder
DECISIVO, IMPORTANTE = 0.30, 0.15


def zona_es(nota):
    return ZONAS_ES.get((nota or "").strip().lower(), nota)


def _es_mala(nota):
    n = (nota or "").lower()
    return "releg" in n or "eliminat" in n


def objetivos_de(filas):
    """Objetivos de la liga según sus zonas: (nombre, k) = terminar entre los k primeros."""
    f = sorted(filas, key=lambda x: x["pos"])
    obj = [("Título", 1)]
    # zonas buenas: de arriba hacia abajo y sin huecos. Una nota suelta a mitad de tabla (por ejemplo, el campeón
    # de copa que ESPN marca con plaza europea) NO es un corte de la tabla y se ignora.
    i = 0
    while i < len(f) and f[i].get("nota") and not _es_mala(f[i]["nota"]):
        j = i
        while j + 1 < len(f) and f[j + 1].get("nota") == f[i]["nota"]:
            j += 1
        obj.append((zona_es(f[i]["nota"]), f[j]["pos"]))
        i = j + 1
    # zonas malas: de abajo hacia arriba y sin huecos
    malas, i = [], len(f) - 1
    while i >= 0 and _es_mala(f[i].get("nota")):
        j = i
        while j - 1 >= 0 and f[j - 1].get("nota") == f[i]["nota"]:
            j -= 1
        malas.append((f[i]["nota"], f[j]["pos"]))
        i = j - 1
    for nota, ini in reversed(malas):
        if ini > 1:
            art = "la " if zona_es(nota).startswith("promoción") else "el "
            obj.append((f"Evitar {art}{zona_es(nota)}", ini - 1))
    vistos, out = set(), []
    for n, k in obj:                       # sin repetir el mismo corte (p. ej. título y ascenso con 1 plaza)
        if k not in vistos and k < len(filas):
            vistos.add(k)
            out.append((n, k))
    return out


class Liga:
    """Situación de una liga: tabla, objetivos y (si se puede) la simulación de lo que falta."""

    def __init__(self, lg, filas):
        self.lg, self.filas = lg, sorted(filas, key=lambda x: x["pos"])
        self.ids = [f["id"] for f in self.filas]
        self.pos_de = {t: i for i, t in enumerate(self.ids)}
        self.objetivos = objetivos_de(self.filas)
        self.simulada = False
        self.motivo = "esta liga no es de ida y vuelta simple o ESPN no da su calendario completo"

    def simular(self, D, P, factor_goles=1.0):
        """Simula los partidos que faltan. Devuelve False (y deja el motivo) si la estructura no cuadra."""
        N = len(self.ids)
        dentro = set(self.ids)
        jugados = [e for e in D.eventos if e["lg"] == self.lg and e.get("fin") and e["h"]["id"] in dentro
                   and e["a"]["id"] in dentro]
        if not jugados or any(t not in D.idx for t in self.ids):
            self.motivo = "faltan partidos o equipos de esta liga en los datos"
            return False
        temporada = max(jugados, key=lambda e: e["fecha"]).get("temporada")
        hechos = {(e["h"]["id"], e["a"]["id"]) for e in jugados if e.get("temporada") == temporada}
        faltan = [(a, b) for a in self.ids for b in self.ids if a != b and (a, b) not in hechos]
        resto = {t: sum(1 for a, b in faltan if t in (a, b)) for t in self.ids}
        # control: lo jugado según la tabla + lo que falta tiene que dar una temporada completa (± 1 por desfases)
        if any(abs(f["pj"] + resto[f["id"]] - 2 * (N - 1)) > 1 for f in self.filas):
            self.motivo = "la tabla y el calendario no cuadran con una liga de ida y vuelta"
            return False
        self.resto, self.faltan = resto, faltan
        base = np.array([[f["pts"], f["dg"], f["gf"]] for f in self.filas], dtype=float)
        S, R = SIMULACIONES, len(faltan)
        rng = np.random.default_rng(zlib.crc32(f"{self.lg}|{temporada}|{sum(f['pj'] for f in self.filas)}".encode()))
        if R:
            c = D.cidx.get(self.lg)
            hi = np.array([D.idx[a] for a, _ in faltan])
            ai = np.array([D.idx[b] for _, b in faltan])
            Hh = P.Hh[c] if c is not None else 1.15
            Ha = P.Ha[c] if c is not None else 0.90
            lh = P.mu * Hh * P.tha[hi] * P.att[hi] * P.dfn[ai] * factor_goles
            la = P.mu * Ha * P.thd[hi] * P.att[ai] * P.dfn[hi] * factor_goles
            gh, ga = rng.poisson(lh, (S, R)), rng.poisson(la, (S, R))
            A_h, A_a = np.zeros((R, N)), np.zeros((R, N))
            A_h[np.arange(R), [self.pos_de[a] for a, _ in faltan]] = 1
            A_a[np.arange(R), [self.pos_de[b] for _, b in faltan]] = 1
            signo = np.sign(gh - ga)
            pts = base[:, 0] + (3 * (signo > 0) + (signo == 0)) @ A_h + (3 * (signo < 0) + (signo == 0)) @ A_a
            dg = base[:, 1] + (gh - ga) @ A_h + (ga - gh) @ A_a
            gf = base[:, 2] + gh @ A_h + ga @ A_a
            self.signo = signo.astype(np.int8)
        else:
            pts, dg, gf = (np.tile(base[:, i], (S, 1)) for i in range(3))
            self.signo = np.zeros((S, 0), np.int8)
        clave = pts * 1e6 + (dg + 500) * 1e3 + gf + rng.random((S, N))
        orden = np.argsort(-clave, axis=1)
        self.puesto = np.empty((S, N), np.int16)
        self.puesto[np.arange(S)[:, None], orden] = np.arange(1, N + 1)
        self.simulada = True
        return True

    # ---- lectura para un equipo
    def _distancia(self, i, k):
        """Puntos de ventaja (+) o de desventaja (−) respecto al corte del objetivo «entre los k primeros»."""
        f = self.filas
        return f[i]["pts"] - f[k]["pts"] if i < k else f[i]["pts"] - f[k - 1]["pts"]

    def _estado(self, i, k, p):
        if self.simulada:
            mio, maximo = self.filas[i]["pts"], self.filas[i]["pts"] + 3 * self.resto[self.ids[i]]
            otros = [(f["pts"], f["pts"] + 3 * self.resto[f["id"]]) for j, f in enumerate(self.filas) if j != i]
            if sum(1 for _, mx in otros if mx >= mio) <= k - 1:
                return "asegurado"
            if sum(1 for pt, _ in otros if pt > maximo) >= k:
                return "sin opciones"
            if p >= CASI:
                return "casi asegurado"
            if p <= 1 - CASI:
                return "casi sin opciones"
        return "en disputa"

    def equipo(self, equipo_id, partido=None):
        """Situación de un equipo. `partido` = (id local, id visita) si el partido analizado es de esta liga."""
        i = self.pos_de.get(equipo_id)
        if i is None:
            return None
        f = self.filas[i]
        out = {"modo": "simulado" if self.simulada else "descriptivo", "pos": f["pos"], "pts": f["pts"], "pj": f["pj"],
               "equipos": len(self.ids), "zona": zona_es(f.get("nota")) if f.get("nota") else None}
        if not self.simulada:
            out["motivo"] = self.motivo
            out["objetivos"] = [{"n": n, "dist": int(self._distancia(i, k)), "dentro": i < k} for n, k in self.objetivos]
            cerca = [o for o in out["objetivos"] if abs(o["dist"]) <= 6] or out["objetivos"][:1]
            out["lectura"] = f"{f['pos']}.º con {f['pts']} puntos en {f['pj']} partidos. " + "; ".join(
                (f"{o['dist']} puntos de ventaja en «{o['n']}»" if o["dentro"] else f"a {-o['dist']} puntos de «{o['n']}»")
                for o in cerca[:3]) + "."
            return out
        out["rest"] = self.resto[equipo_id]
        r = self.faltan.index(partido) if partido in self.faltan else None
        soy_local = partido is not None and partido[0] == equipo_id
        objs = []
        for n, k in self.objetivos:
            logro = self.puesto[:, i] <= k
            p = float(logro.mean())
            o = {"n": n, "p": round(p, 3), "dist": int(self._distancia(i, k)), "dentro": i < k, "estado": self._estado(i, k, p)}
            if r is not None:
                s = self.signo[:, r] if soy_local else -self.signo[:, r]
                for nombre, val in (("gana", 1), ("empata", 0), ("pierde", -1)):
                    m = s == val
                    o[nombre] = round(float(logro[m].mean()), 3) if m.sum() >= 100 else None
                if o["gana"] is not None and o["pierde"] is not None:
                    o["relevancia"] = round(o["gana"] - o["pierde"], 3)
            objs.append(o)
        vivos = [o for o in objs if o["estado"] == "en disputa" and o.get("relevancia") is not None]
        principal = max(vivos, key=lambda o: o["relevancia"])["n"] if vivos else None
        # se muestran hasta 4: primero el objetivo que más depende de este partido, luego los más inciertos
        visibles = sorted((o for o in objs if 0.005 < o["p"] < 0.995),
                          key=lambda o: (o["n"] != principal, -min(o["p"], 1 - o["p"])))
        out["objetivos"] = visibles[:4] or objs[:1]
        if vivos:
            pr = max(vivos, key=lambda o: o["relevancia"])
            out["principal"] = pr["n"]
            rel = pr["relevancia"]
            # un solo partido rara vez decide una temporada: «decisivo» queda para saltos de 30 puntos o más
            out["nivel"] = "decisivo" if rel >= DECISIVO else "importante" if rel >= IMPORTANTE else "normal"
            pc = lambda x: f"{round(x * 100)} %"      # noqa: E731
            if out["nivel"] == "normal":                  # sin urgencia real: no se habla de «necesitar»
                out["lectura"] = (f"Este partido pesa poco en su tabla: su opción de «{pr['n']}» iría de "
                                  f"{pc(pr['pierde'])} si pierde a {pc(pr['gana'])} si gana (hoy {pc(pr['p'])}).")
                out["sirve_empate"] = None
            elif pr["empata"] is not None and pr["empata"] >= pr["p"] - 0.03:
                out["lectura"] = (f"Le sirve el empate para «{pr['n']}»: su opción quedaría en {pc(pr['empata'])} "
                                  f"(hoy {pc(pr['p'])}); si gana sube a {pc(pr['gana'])} y si pierde baja a {pc(pr['pierde'])}.")
                out["sirve_empate"] = True
            else:
                out["lectura"] = (f"Necesita ganar para «{pr['n']}»: si gana su opción sube a {pc(pr['gana'])}, con el "
                                  f"empate queda en {pc(pr['empata']) if pr['empata'] is not None else '–'} y si pierde "
                                  f"cae a {pc(pr['pierde'])} (hoy {pc(pr['p'])}).")
                out["sirve_empate"] = False
        else:
            hechos = [f"«{o['n']}» {o['estado']}" for o in objs if o["estado"] != "en disputa"][:3]
            en_juego = [o for o in objs if o["estado"] == "en disputa"]
            if en_juego:                                  # hay objetivos vivos, pero este partido no es de su liga
                o = max(en_juego, key=lambda x: min(x["p"], 1 - x["p"]))
                out["nivel"] = "normal"
                out["lectura"] = (f"En su liga es {f['pos']}.º con {f['pts']} puntos; «{o['n']}» sigue en disputa "
                                  f"({round(o['p'] * 100)} %).")
            else:
                out["nivel"] = "sin urgencia"
                out["lectura"] = "Sin urgencia en la tabla: " + ("; ".join(hechos) or "no tiene objetivos en disputa") + "."
        return out


def preparar(tablas, D, P):
    """Arma la situación de cada liga con tabla única y simula las de ida y vuelta."""
    ligas, de_equipo = {}, {}
    fg = getattr(P, "factor_goles", 1.0)
    for lg, grupos in (tablas or {}).items():
        if len(grupos) != 1 or len(grupos[0].get("filas") or []) < 6:
            continue
        L = Liga(lg, grupos[0]["filas"])
        if lg in IDA_Y_VUELTA:
            try:
                L.simular(D, P, fg)
            except Exception as ex:                       # nunca debe tumbar la actualización
                L.simulada, L.motivo = False, f"no se pudo simular ({type(ex).__name__})"
        ligas[lg] = L
        for t in L.ids:
            de_equipo.setdefault(t, lg)
    return {"ligas": ligas, "de_equipo": de_equipo}


def _serie(e, ida):
    """Eliminatoria de ida y vuelta: marcador global antes de este partido."""
    if ida.get("local") == e["a"]["n"]:                   # la ida se jugó en casa del visitante de hoy
        hoy_local, hoy_visita = ida["gv"], ida["gl"]
    else:
        hoy_local, hoy_visita = ida["gl"], ida["gv"]
    d = hoy_local - hoy_visita
    if d == 0:
        txt = "Serie igualada tras la ida: quien gane pasa; el empate lleva a prórroga o penales según el torneo."
    else:
        abajo, arriba = (e["a"]["n"], e["h"]["n"]) if d > 0 else (e["h"]["n"], e["a"]["n"])
        txt = (f"{arriba} ganó la ida por {abs(d)}: a {abajo} le hace falta ganar por {abs(d)} para igualar la serie "
               f"y por {abs(d) + 1} para pasar sin prórroga ni penales.")
    return {"global": [hoy_local, hoy_visita], "lectura": txt}


def de_partido(nec, e, ctx=None):
    """Necesidad de los dos equipos en este partido (o None si no hay tabla ni eliminatoria)."""
    out = {}
    if nec:
        L = nec["ligas"].get(e["lg"])
        for s in ("h", "a"):
            tid = e[s]["id"]
            if L and tid in L.pos_de:                     # partido de liga: lo que se juega aquí
                out[s] = L.equipo(tid, (e["h"]["id"], e["a"]["id"]))
            else:                                         # copa u otro torneo: su situación en su liga, como contexto
                lg = nec["de_equipo"].get(tid)
                if lg:
                    x = nec["ligas"][lg].equipo(tid)
                    if x:
                        x["otra_liga"] = lg
                        out[s] = x
    if ctx and ctx.get("ida"):
        try:
            out["serie"] = _serie(e, ctx["ida"])
        except (KeyError, TypeError):
            pass
    return out or None
