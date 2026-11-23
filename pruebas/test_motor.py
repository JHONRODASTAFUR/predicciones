"""Pruebas automáticas del motor.  Ejecutar desde la carpeta del proyecto:

    python -m unittest discover -s pruebas -v

No usan internet. Las de la clase DatosReales revisan el archivo datos/predicciones.json si existe.
"""
import collections
import datetime as dt
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from motor import apuestas, auditoria, goles, inteligencia, modelo, necesidad, simulacion  # noqa: E402


def matriz(lh=1.45, la=1.10, rho=-0.05):
    return modelo.matriz(np.array([lh]), np.array([la]), rho)[0]


class Simulacion(unittest.TestCase):
    def setUp(self):
        self.M = matriz()
        self.r = simulacion.simular(self.M, "partido-de-prueba", prop_1t=0.44,
                                    corners={"local": 5.6, "visita": 4.2, "phi": 1.3},
                                    tarjetas={"local": 2.0, "visita": 2.6, "phi": 1.0})

    def test_son_diez_mil(self):
        self.assertEqual(self.r["n"], 10_000)

    def test_coincide_con_la_matriz_exacta(self):
        ex = simulacion.exactas_de(self.M)
        for k in simulacion.CLAVES_CONTROL:
            self.assertAlmostEqual(self.r["p"][k], ex[k], delta=0.015, msg=k)
        self.assertTrue(self.r["estabilidad"]["estable"])

    def test_intervalo_contiene_el_valor_exacto(self):
        ex = simulacion.exactas_de(self.M)
        dentro = sum(lo - 0.005 <= ex[k] <= hi + 0.005 for k, (lo, hi) in self.r["ic"].items())
        self.assertGreaterEqual(dentro, 4)          # un intervalo del 95 % puede fallar alguna vez

    def test_se_puede_repetir(self):
        otra = simulacion.simular(self.M, "partido-de-prueba", prop_1t=0.44,
                                  corners={"local": 5.6, "visita": 4.2, "phi": 1.3},
                                  tarjetas={"local": 2.0, "visita": 2.6, "phi": 1.0})
        self.assertEqual(self.r, otra)
        distinta = simulacion.simular(self.M, "otro-partido")
        self.assertNotEqual(self.r["p"]["1"], distinta["p"]["1"])

    def test_coherencia_interna(self):
        p = self.r["p"]
        self.assertAlmostEqual(p["1"] + p["X"] + p["2"], 1.0, places=3)
        self.assertAlmostEqual(p["PRIMERO_L"] + p["PRIMERO_V"] + p["PRIMERO_NADIE"], 1.0, places=3)
        self.assertAlmostEqual(p["1T_1"] + p["1T_X"] + p["1T_2"], 1.0, places=3)
        self.assertLessEqual(p["MIT_L_AMBAS"], p["MIT_L"])
        self.assertLessEqual(p["MIT_L_AMBAS"], p["1"] + 1e-9)       # quien gana las dos mitades gana el partido
        self.assertGreaterEqual(p["O0.5"], p["O1.5"])
        self.assertGreaterEqual(p["cor_O5.5"], p["cor_O10.5"])

    def test_primer_tiempo_segun_la_proporcion(self):
        esperado = 1 - math.exp(-(1.45 + 1.10) * 0.44)              # Poisson: P(al menos un gol en el 1.er tiempo)
        self.assertAlmostEqual(self.r["p"]["1T_O0.5"], esperado, delta=0.03)

    def test_medias_de_corners_y_tarjetas(self):
        self.assertAlmostEqual(self.r["medias"]["cor"], 9.8, delta=0.2)
        self.assertAlmostEqual(self.r["medias"]["tarj"], 4.6, delta=0.15)

    def test_wilson(self):
        lo, hi = simulacion.wilson(5000, 10_000)
        self.assertAlmostEqual(lo, 0.4902, places=3)
        self.assertAlmostEqual(hi, 0.5098, places=3)


class Cuotas(unittest.TestCase):
    def test_probabilidad_implicita_sin_margen(self):
        p = inteligencia._sin_margen([2.0, 3.5, 4.0])
        self.assertAlmostEqual(sum(p), 1.0)
        self.assertGreater(1 / 2.0, p[0])            # la implícita cruda (1/cuota) lleva el margen de la casa

    def test_valor_esperado(self):
        # EV = P(acierto) × ganancia neta − P(fallo) × importe;  p=0,55 a cuota 2,00 → +0,10 por sol
        self.assertAlmostEqual(apuestas.ev(0.55, 0.0, 2.0), 0.10, places=6)
        self.assertLess(apuestas.ev(0.45, 0.0, 2.0), 0)

    def test_movimiento_de_cuotas(self):
        mov, e = {}, {"id": "1", "fecha": "2026-11-21T20:00Z"}
        self.assertIsNone(inteligencia.movimiento(mov, e, {"cuotas": {"h": 2.0, "x": 3.4, "a": 3.8}}, "t1"))
        self.assertIsNone(inteligencia.movimiento(mov, e, {"cuotas": {"h": 2.0, "x": 3.4, "a": 3.8}}, "t2"))
        self.assertEqual(len(mov["1"]["fotos"]), 1)   # misma cuota: no se guarda otra foto
        mv = inteligencia.movimiento(mov, e, {"cuotas": {"h": 1.8, "x": 3.6, "a": 4.4}}, "t3")
        self.assertGreater(mv["dH"], 0)               # baja la cuota del local → sube su probabilidad
        self.assertLess(mv["dA"], 0)
        self.assertEqual(mv["antes"], [2.0, 3.4, 3.8])


class Clasificacion(unittest.TestCase):
    def test_clases(self):
        bajo = {"riesgo": "bajo", "motivos": []}
        self.assertEqual(inteligencia.clase_de({"p": 0.80, "p10": 0.72}, bajo)[0], "conservadora")
        self.assertEqual(inteligencia.clase_de({"p": 0.60, "p10": 0.50}, bajo)[0], "moderada")
        self.assertEqual(inteligencia.clase_de({"p": 0.40, "p10": 0.30}, bajo)[0], "elevado")
        self.assertEqual(inteligencia.clase_de({"p": 0.80, "p10": 0.72, "no_calibrado": True}, bajo)[0], "sin_valor")
        self.assertEqual(inteligencia.clase_de({"p": 0.80, "p10": 0.72}, {"riesgo": "alto", "motivos": ["x"]})[0],
                         "sin_valor")
        # datos dudosos: nunca «conservadora» aunque la probabilidad sea alta
        self.assertEqual(inteligencia.clase_de({"p": 0.80, "p10": 0.72}, {"riesgo": "medio"})[0], "moderada")

    def test_orientacion(self):
        self.assertEqual(inteligencia.orientacion("1X"), ("h", None))
        self.assertEqual(inteligencia.orientacion("AH2+1.5"), ("a", None))
        self.assertEqual(inteligencia.orientacion("O2.5"), (None, "mas"))
        self.assertEqual(inteligencia.orientacion("BTTS_NO"), (None, "menos"))
        self.assertEqual(inteligencia.orientacion("V_O0.5"), ("a", None))

    def _item(self, riesgo="bajo", no_cal=False):
        fila = {"cod": "1", "txt": "Gana A", "p": 0.78, "p10": 0.70, "p90": 0.85, "minima": 1.35, "alerta": 1.6}
        if no_cal:
            fila["no_calibrado"] = True
        return {"id": "9", "h": {"id": "a", "n": "A"}, "a": {"id": "b", "n": "B"},
                "pred": {"pH": 0.78, "pD": 0.14, "pA": 0.08, "lh": 2.1, "la": 0.7, "conf": "alta"},
                "mercado": {"pH": 0.60, "pD": 0.25, "pA": 0.15, "cuotas": {"h": 1.6, "x": 4.0, "a": 6.5}},
                "ctx": {"h": {"forma": "GGGGE"}, "a": {"forma": "PPPEP"}}, "alineacion": None, "senales": [],
                "apuesta": {"base": "mercado", "mercados": [fila],
                            "perfiles": {"conservador": {"cod": "1", "riesgo": riesgo, "motivos": ["motivo"]}}}}

    def test_veredicto(self):
        e = {"id": "9", "fecha": "2026-11-21T20:00Z", "h": {"id": "a", "n": "A"}, "a": {"id": "b", "n": "B"}}
        ok = inteligencia.analizar(self._item(), e, {})
        self.assertEqual(ok["veredicto"]["cod"], "condicional")
        self.assertEqual(ok["perfiles"]["conservador"]["clase"], "conservadora")
        self.assertTrue(any("1,35" in t for t in ok["perfiles"]["conservador"]["invalidan"]))
        self.assertTrue(any("modelo" in d["texto"] for d in ok["discrepancias"]))     # 78 % contra 60 %
        for item in (self._item(riesgo="alto"), self._item(no_cal=True)):
            self.assertEqual(inteligencia.analizar(item, e, {})["veredicto"]["txt"], "NO HAY APUESTA RECOMENDADA")
        sin = self._item()
        sin["apuesta"] = None
        self.assertEqual(inteligencia.analizar(sin, e, {})["veredicto"]["cod"], "no")


class _D:
    """Datos mínimos de mentira para probar la necesidad en la tabla (una liga de 6 equipos a ida y vuelta)."""

    def __init__(self, jornadas_jugadas):
        self.ids = [str(i) for i in range(6)]
        self.idx = {t: i for i, t in enumerate(self.ids)}
        self.cidx = {"prueba.1": 0}
        cruces = [(a, b) for a in self.ids for b in self.ids if a != b]            # 30 partidos
        self.eventos = [{"lg": "prueba.1", "fin": True, "temporada": 2026, "fecha": f"2026-09-{k + 1:02d}T20:00Z",
                         "h": {"id": a}, "a": {"id": b}} for k, (a, b) in enumerate(cruces[:jornadas_jugadas])]


class _P:
    def __init__(self, fuerza):
        n = len(fuerza)
        self.att, self.dfn = np.array(fuerza, float), 1 / np.array(fuerza, float)
        self.tha = self.thd = np.ones(n)
        self.Hh, self.Ha, self.mu = np.array([1.15]), np.array([0.9]), 1.3


def _tabla(D, puntos):
    pj = collections.Counter(t for e in D.eventos for t in (e["h"]["id"], e["a"]["id"]))
    orden = sorted(D.ids, key=lambda t: -puntos[t])
    notas = {1: "Champions League", 2: "Champions League", 6: "Relegation"}
    return [{"id": t, "n": "Equipo " + t, "pos": i + 1, "pj": pj[t], "pts": puntos[t], "dg": 0, "gf": 10,
             "nota": notas.get(i + 1)} for i, t in enumerate(orden)]


class Necesidad(unittest.TestCase):
    def setUp(self):
        self.D = _D(20)                                   # faltan 10 partidos
        self.P = _P([1.6, 1.3, 1.0, 1.0, 0.8, 0.6])
        self.pts = {"0": 16, "1": 14, "2": 9, "3": 8, "4": 6, "5": 2}
        necesidad.IDA_Y_VUELTA.add("prueba.1")
        self.nec = necesidad.preparar({"prueba.1": [{"nombre": "Liga", "filas": _tabla(self.D, self.pts)}]}, self.D, self.P)
        self.L = self.nec["ligas"]["prueba.1"]

    def tearDown(self):
        necesidad.IDA_Y_VUELTA.discard("prueba.1")

    def test_objetivos_salen_de_las_zonas(self):
        self.assertEqual(self.L.objetivos, [("Título", 1), ("Champions League", 2), ("Evitar el descenso", 5)])

    def test_simula_y_las_probabilidades_tienen_sentido(self):
        self.assertTrue(self.L.simulada)
        self.assertEqual(sum(self.L.resto.values()), 2 * len(self.L.faltan))
        titulo = {t: float((self.L.puesto[:, i] == 1).mean()) for i, t in enumerate(self.L.ids)}
        self.assertAlmostEqual(sum(titulo.values()), 1.0, places=6)          # un solo campeón por temporada simulada
        self.assertGreater(titulo["0"], titulo["1"])
        self.assertGreater(titulo["1"], titulo["3"])

    def test_gana_empata_pierde(self):
        partido = self.L.faltan[0]
        x = self.L.equipo(partido[0], partido)
        self.assertEqual(x["modo"], "simulado")
        self.assertIn("lectura", x)
        for o in x["objetivos"]:
            if o.get("gana") is not None and o.get("pierde") is not None:
                self.assertGreaterEqual(o["gana"] + 0.03, o["pierde"])     # ganar nunca perjudica el objetivo

    def test_objetivo_asegurado_o_sin_opciones(self):
        D = _D(28)                                        # quedan 2 partidos
        pts = {"0": 30, "1": 20, "2": 12, "3": 10, "4": 8, "5": 1}
        L = necesidad.preparar({"prueba.1": [{"nombre": "Liga", "filas": _tabla(D, pts)}]}, D, self.P)["ligas"]["prueba.1"]
        lider = L.equipo("0")
        self.assertEqual(lider["nivel"], "sin urgencia")
        estados = {o["n"]: L._estado(0, k, 1.0) for o, (_, k) in zip([{"n": n} for n, _ in L.objetivos], L.objetivos)}
        self.assertEqual(estados["Título"], "asegurado")
        self.assertEqual(L._estado(5, 1, 0.0), "sin opciones")

    def test_modo_descriptivo_si_no_es_ida_y_vuelta(self):
        necesidad.IDA_Y_VUELTA.discard("prueba.1")
        nec = necesidad.preparar({"prueba.1": [{"nombre": "Liga", "filas": _tabla(self.D, self.pts)}]}, self.D, self.P)
        x = necesidad.de_partido(nec, {"lg": "prueba.1", "h": {"id": "2", "n": "A"}, "a": {"id": "5", "n": "B"}})
        self.assertEqual(x["h"]["modo"], "descriptivo")
        self.assertNotIn("p", x["h"]["objetivos"][0])                       # sin simulación no se inventan porcentajes
        self.assertIn("puntos", x["h"]["lectura"])

    def test_eliminatoria_de_ida_y_vuelta(self):
        e = {"lg": "copa", "h": {"id": "x", "n": "Local"}, "a": {"id": "y", "n": "Visita"}}
        s = necesidad.de_partido(None, e, {"ida": {"local": "Visita", "gl": 2, "gv": 0, "visita": "Local"}})["serie"]
        self.assertEqual(s["global"], [0, 2])
        self.assertIn("ganar por 2", s["lectura"])


class FactorDeGoles(unittest.TestCase):
    def _filas(self, sesgo, n=3600):
        rng = np.random.default_rng(5)
        filas, fechas = [], {}
        for k in range(n):
            lh, la = 1.5, 1.1
            gh, ga = rng.poisson(lh * sesgo), rng.poisson(la * sesgo)
            fechas[str(k)] = (dt.date(2026, 1, 5) + dt.timedelta(days=k * 126 // n)).isoformat()      # 18 semanas
            filas.append(("liga", 0.45, 0.27, 0.28, gh, ga, 0.45, 0.27, 0.28, {"id": str(k), "lh": lh, "la": la, "rho": -0.03}))
        return filas, fechas

    def test_corrige_si_el_modelo_se_queda_corto(self):
        filas, fechas = self._filas(1.08)
        nuevas, r = goles.ajustar_validacion(filas, fechas, modelo.matriz, modelo.resumen_matriz)
        self.assertTrue(r["aplicado"])
        self.assertGreater(r["factor"], 1.03)
        self.assertLessEqual(r["factor"], goles.TOPE[1])
        self.assertLess(r["logloss_o25"][1], r["logloss_o25"][0])
        self.assertGreater(nuevas[-1][9]["lh"], filas[-1][9]["lh"])
        self.assertEqual(nuevas[0][9]["lh"], filas[0][9]["lh"])              # las primeras semanas no se corrigen

    def test_no_toca_un_modelo_bien_calibrado(self):
        filas, fechas = self._filas(1.0)
        _, r = goles.ajustar_validacion(filas, fechas, modelo.matriz, modelo.resumen_matriz)
        self.assertLess(abs(r["factor_medido"] - 1), 0.03)
        self.assertLess(abs(r["factor"] - 1), 0.03)


class Auditoria(unittest.TestCase):
    def _item(self, cuota_local, estado="probable", bajas=()):
        jug = [{"n": n, "posible_baja": {"tipo": "duda"}} for n in bajas]
        return {"fecha": "2026-11-25T20:00Z", "pred": {"pH": 0.5, "pD": 0.27, "pA": 0.23, "lh": 1.5, "la": 1.0},
                "mercado": {"pH": 0.5, "pD": 0.27, "pA": 0.23, "cuotas": {"h": cuota_local, "x": 3.4, "a": 4.0}},
                "alineacion": {"estado": estado, "h": {"jugadores": jug, "sale": ["Titular X"]}, "a": {"jugadores": [], "sale": []}},
                "apuesta": {"base": "mercado", "mercados": [{"cod": "1", "p": 0.5, "cuota_casa": cuota_local}],
                            "perfiles": {"equilibrado": {"cod": "1"}}}}

    def test_tres_momentos_y_que_cambio(self):
        reg, T = {}, lambda h: dt.datetime(2026, 11, 25, 20, tzinfo=dt.timezone.utc) - dt.timedelta(hours=h)   # noqa: E731
        auditoria.registrar(reg, self._item(2.00), T(60))
        self.assertNotIn("momentos", reg)                                   # a más de 48 h aún no hay momento
        auditoria.registrar(reg, self._item(2.00), T(30))
        auditoria.registrar(reg, self._item(1.85, bajas=["Delantero Z"]), T(4))
        auditoria.registrar(reg, self._item(1.80, estado="confirmada"), T(1))
        self.assertEqual(list(reg["momentos"]), ["48h", "6h", "oficial"])
        self.assertEqual([(c["de"], c["a"]) for c in reg["cambios"]], [("48h", "6h"), ("6h", "oficial")])
        self.assertTrue(any("cuotas pasaron" in m for m in reg["cambios"][0]["motivos"]))
        self.assertTrue(any("Delantero Z" in m for m in reg["cambios"][0]["motivos"]))
        self.assertTrue(any("alineación oficial" in m and "Titular X" in m for m in reg["cambios"][1]["motivos"]))
        self.assertEqual(reg["cuotas_ini"], [2.00, 3.4, 4.0])
        self.assertEqual(reg["apuesta_ini"]["equilibrado"], {"cod": "1", "p": 0.5, "cuota": 2.00, "cierre": 1.80})
        auditoria.liquidar(reg, 2, 0, modelo.liquidar)
        self.assertEqual(reg["apuesta_ini"]["equilibrado"]["res"], "gana")

    def test_resumen(self):
        rng, hist = np.random.default_rng(3), {}
        for k in range(400):
            gana = rng.random() < 0.55
            p = {"pH": 0.55, "pD": 0.25, "pA": 0.20, "o25": 0.5, "btts": 0.5}
            hist[str(k)] = {"lg": "a.1" if k % 2 else "b.1", "fecha": f"2026-11-{k % 20 + 1:02d}T20:00Z", "ultima": p, "primera": p,
                            "mercado": {"pH": 0.5, "pD": 0.27, "pA": 0.23}, "res": [2, 0] if gana else [0, 1],
                            "apuesta": {"perfiles": {"equilibrado": {"cod": "1", "p": 0.55, "justa": 1.82, "riesgo": "bajo", "clase": "moderada",
                                                                     "cuota_casa": 1.9, "res": "gana" if gana else "pierde"}}},
                            "apuesta_ini": {"equilibrado": {"cod": "1", "p": 0.55, "cuota": 2.0, "cierre": 1.9}}}
        r = auditoria.resumen(hist)
        self.assertEqual(r["n"], 400)
        self.assertAlmostEqual(r["ultima"]["acierto"], 0.55, delta=0.07)
        self.assertEqual(r["sesgos"][0]["n"], "Gana el local")
        self.assertAlmostEqual(r["clv"]["medio"], 2.0 / 1.9 - 1, places=3)   # se tomó 2,00 y cerró en 1,90: CLV positivo
        g = r["perfiles"]["equilibrado"]
        self.assertEqual(g["n"], 400)
        self.assertIn("moderada", g["por_clase"])
        self.assertEqual(r["ligas"]["medidas"], 2)
        self.assertEqual(auditoria.resumen({})["n"], 0)


class PaqueteWeb(unittest.TestCase):
    def test_los_partidos_lejanos_viajan_livianos(self):
        try:
            import publicar_web
        except ImportError:
            self.skipTest("falta la librería cryptography")
        ahora = dt.datetime(2026, 11, 22, 12, tzinfo=dt.timezone.utc)

        def partido(i, dias):
            return {"id": i, "estado": "pre", "fecha": (ahora + dt.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%MZ"),
                    "pred": {"pH": 0.5, "conf": "media", "calc": {"mu": 1.3}}, "matriz": [[1.0]], "extras": {"x": 1},
                    "ctx": {"h": {"forma": "GGE", "ultimos": [1, 2]}, "a": {"forma": "PPG"}},
                    "alineacion": {"estado": "probable", "h": {"jugadores": [1] * 11}},
                    "sim": {"n": 10000, "p": {"1": 0.5}, "estabilidad": {"estable": True}},
                    "intel": {"veredicto": {"cod": "condicional"}, "puntaje": 80, "escenarios": [1],
                              "perfiles": {"equilibrado": {"cod": "1", "clase": "moderada", "evidencia": 70, "motivo": "m", "favorables": ["x"]}}},
                    "apuesta": {"perfiles": {"equilibrado": {"cod": "1"}}, "mercados": [{"cod": "1"}, {"cod": "X"}, {"cod": "2"}]}}
        datos = {"partidos": [partido("cerca", 1), partido("lejos", 5), {"id": "fin", "estado": "post", "fecha": "2026-11-20T20:00Z"}]}
        lejos = publicar_web.aligerar(datos, ahora)
        cerca, lej = datos["partidos"][0], datos["partidos"][1]
        self.assertEqual(list(lejos), ["lejos"])
        self.assertNotIn("_lejos", cerca)
        self.assertEqual(len(cerca["apuesta"]["mercados"]), 3)                # el partido cercano va completo
        self.assertTrue(lej["_lejos"])
        self.assertEqual([f["cod"] for f in lej["apuesta"]["mercados"]], ["1"])   # solo la fila de la apuesta elegida
        self.assertEqual(lej["intel"]["perfiles"]["equilibrado"]["clase"], "moderada")
        self.assertEqual(lej["ctx"]["h"], {"forma": "GGE"})
        self.assertNotIn("matriz", lej)
        self.assertNotIn("calc", lej["pred"])
        # nada se pierde: todo lo retirado está en el segundo archivo
        d = lejos["lejos"]
        self.assertEqual(len(d["mercados"]), 3)
        self.assertEqual(d["calc"], {"mu": 1.3})
        self.assertEqual(d["sim"]["p"], {"1": 0.5})
        self.assertEqual(d["intel"]["escenarios"], [1])
        self.assertEqual(len(d["alineacion"]["h"]["jugadores"]), 11)


class DatosReales(unittest.TestCase):
    """Control de calidad sobre lo que la app va a mostrar."""

    @classmethod
    def setUpClass(cls):
        ruta = BASE / "datos" / "predicciones.json"
        if not ruta.exists():
            raise unittest.SkipTest("todavía no hay datos/predicciones.json")
        cls.d = json.loads(ruta.read_text(encoding="utf-8"))
        cls.pre = [m for m in cls.d["partidos"] if m["estado"] == "pre"]
        if not any(m.get("sim") for m in cls.pre):
            raise unittest.SkipTest("predicciones.json es de una versión anterior (sin simulación)")

    def test_probabilidades_suman_uno(self):
        for m in self.d["partidos"]:
            p = m["pred"]
            self.assertAlmostEqual(p["pH"] + p["pD"] + p["pA"], 1.0, delta=0.002, msg=m["id"])

    def test_toda_simulacion_es_estable(self):
        con = [m for m in self.pre if m.get("sim")]
        self.assertGreater(len(con), 0)
        malas = [m["id"] for m in con if not m["sim"]["estabilidad"]["estable"]]
        # 3 errores estándar en 5 probabilidades: por puro azar se sale cerca del 1 % de los partidos
        self.assertLessEqual(len(malas), len(con) * 0.03, malas[:5])
        self.assertTrue(all(m["sim"]["n"] >= 10_000 for m in con))

    def test_todo_partido_tiene_veredicto_y_clase(self):
        for m in self.pre:
            intel = m.get("intel")
            self.assertIsNotNone(intel, m["id"])
            self.assertIn(intel["veredicto"]["cod"], ("no", "condicional"))
            for x in intel["perfiles"].values():
                self.assertIn(x["clase"], inteligencia.NOMBRE_CLASE)
                self.assertTrue(0 <= x["evidencia"] <= 100)

    def test_no_se_recomienda_lo_no_calibrado(self):
        for m in self.pre:
            por_cod = {f["cod"]: f for f in (m.get("apuesta") or {}).get("mercados", [])}
            for x in (m.get("intel") or {}).get("perfiles", {}).values():
                if por_cod.get(x["cod"], {}).get("no_calibrado"):
                    self.assertEqual(x["clase"], "sin_valor")

    def test_auditoria_y_necesidad(self):
        if self.d.get("version_modelo", "0") < "3.1":
            self.skipTest("predicciones.json es anterior a la versión 3.1")
        a = self.d["auditoria"]
        self.assertGreater(a["n"], 0)
        for s in a.get("sesgos", []):
            self.assertTrue(0 <= s["pred"] <= 1 and 0 <= s["real"] <= 1)
        self.assertIn("aplicado", self.d["goles"])
        self.assertTrue(0.95 <= self.d["goles"]["factor"] <= 1.10)
        con = [m for m in self.pre if m.get("necesidad")]
        self.assertGreater(len(con), 0)
        for m in con:
            for s in ("h", "a"):
                x = m["necesidad"].get(s)
                if x and x["modo"] == "simulado":
                    for o in x["objetivos"]:
                        self.assertTrue(0 <= o["p"] <= 1, m["id"])
                elif x:
                    self.assertTrue(all("p" not in o for o in x["objetivos"]), m["id"])   # sin simular no hay porcentajes

    def test_cada_fuente_tiene_nombre_y_uso(self):
        for f in self.d["fuentes"]:
            self.assertTrue(f["nombre"] and f["uso"])
        self.assertTrue(self.d["generado"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
