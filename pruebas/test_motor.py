"""Pruebas automáticas del motor.  Ejecutar desde la carpeta del proyecto:

    python -m unittest discover -s pruebas -v

No usan internet. Las de la clase DatosReales revisan el archivo datos/predicciones.json si existe.
"""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from motor import apuestas, inteligencia, modelo, simulacion  # noqa: E402


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

    def test_cada_fuente_tiene_nombre_y_uso(self):
        for f in self.d["fuentes"]:
            self.assertTrue(f["nombre"] and f["uso"])
        self.assertTrue(self.d["generado"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
