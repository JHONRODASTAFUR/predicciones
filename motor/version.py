"""Versión del modelo. Se guarda junto a cada predicción del historial para saber con qué versión se hizo.

Para volver a una versión anterior del programa:  git checkout <etiqueta>   (ver etiquetas con: git tag)
La etiqueta «antes-transformacion-2026-11-20» es el programa tal como estaba antes de la versión 3.
"""

VERSION_MODELO = "3.1.0"

CAMBIOS = {
    "3.1.0": "Auditoría del 22/11/2026. Factor de goles reciente (el modelo se quedaba corto ~3-6 %; se aplica solo "
             "si mejora semana a semana). Necesidad en la tabla, tres momentos con registro de cambios, auditoría "
             "permanente (calibración, ROI, CLV) y abstención en ligas donde el modelo no supera la referencia. "
             "Los empates NO se tocaron: en 23 340 partidos fuera de muestra el desvío es azar.",
    "3.0.0": "Simulación Monte Carlo en Python (10 000 escenarios con prueba de estabilidad), clasificación de riesgo, "
             "veredicto «no hay apuesta», discrepancias, movimiento de cuotas y escenarios. Las probabilidades 1X2 y "
             "de goles NO cambian respecto a la 2.x (mismo modelo, misma ancla al mercado).",
    "2.x": "Dixon-Coles con decaimiento temporal, conjunto con Elo en ligas de nivel 3-4 y ancla a las cuotas.",
}
