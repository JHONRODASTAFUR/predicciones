"""Configuración general: rutas, ligas y parámetros del modelo."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATOS = BASE / "datos"
CACHE = DATOS / "cache"
LOGS = BASE / "logs"
WEB = BASE / "web"

ARCHIVO_PREDICCIONES = DATOS / "predicciones.json"
ARCHIVO_ESTADO = DATOS / "estado.json"
ARCHIVO_HISTORIAL = DATOS / "historial.json"
ARCHIVO_GEO = DATOS / "geo.json"
ARCHIVO_AJUSTE = DATOS / "ajuste_modelo.json"
ARCHIVO_LOCK = DATOS / "actualizando.lock"

for _d in (DATOS, CACHE, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

# (código ESPN, nombre, país/región, nivel de énfasis 1-3, tipo)
# Nivel 1 = destacadas (lo que pediste priorizar), 2 = grandes, 3 = resto.
LIGAS = [
    ("uefa.champions", "Champions League", "Europa", 1, "copa"),
    ("esp.1", "LaLiga", "España", 1, "liga"),
    ("eng.1", "Premier League", "Inglaterra", 1, "liga"),
    ("fra.1", "Ligue 1", "Francia", 1, "liga"),
    ("ita.1", "Serie A", "Italia", 1, "liga"),
    ("ger.1", "Bundesliga", "Alemania", 1, "liga"),
    ("uefa.europa", "Europa League", "Europa", 2, "copa"),
    ("uefa.europa.conf", "Conference League", "Europa", 2, "copa"),
    ("conmebol.libertadores", "Copa Libertadores", "Sudamérica", 2, "copa"),
    ("conmebol.sudamericana", "Copa Sudamericana", "Sudamérica", 2, "copa"),
    ("per.1", "Liga 1", "Perú", 2, "liga"),
    ("por.1", "Primeira Liga", "Portugal", 2, "liga"),
    ("ned.1", "Eredivisie", "Países Bajos", 2, "liga"),
    ("bra.1", "Brasileirão", "Brasil", 2, "liga"),
    ("arg.1", "Liga Profesional", "Argentina", 2, "liga"),
    ("tur.1", "Süper Lig", "Turquía", 3, "liga"),
    ("bel.1", "Pro League", "Bélgica", 3, "liga"),
    ("sco.1", "Premiership", "Escocia", 3, "liga"),
    ("eng.2", "Championship", "Inglaterra", 3, "liga"),
    ("esp.2", "LaLiga 2", "España", 3, "liga"),
    ("ita.2", "Serie B", "Italia", 3, "liga"),
    ("ger.2", "2. Bundesliga", "Alemania", 3, "liga"),
    ("fra.2", "Ligue 2", "Francia", 3, "liga"),
    ("gre.1", "Super League", "Grecia", 3, "liga"),
    ("aut.1", "Bundesliga", "Austria", 3, "liga"),
    ("den.1", "Superliga", "Dinamarca", 3, "liga"),
    ("nor.1", "Eliteserien", "Noruega", 3, "liga"),
    ("swe.1", "Allsvenskan", "Suecia", 3, "liga"),
    ("rus.1", "Premier Liga", "Rusia", 3, "liga"),
    ("usa.1", "MLS", "EE. UU.", 3, "liga"),
    ("mex.1", "Liga MX", "México", 3, "liga"),
    ("col.1", "Primera A", "Colombia", 3, "liga"),
    ("chi.1", "Primera División", "Chile", 3, "liga"),
    ("uru.1", "Primera División", "Uruguay", 3, "liga"),
    ("ecu.1", "LigaPro", "Ecuador", 3, "liga"),
    ("par.1", "Primera División", "Paraguay", 3, "liga"),
    ("bol.1", "División Profesional", "Bolivia", 3, "liga"),
    ("ven.1", "Liga FUTVE", "Venezuela", 3, "liga"),
    ("ksa.1", "Saudi Pro League", "Arabia Saudita", 3, "liga"),
    ("jpn.1", "J1 League", "Japón", 3, "liga"),
    ("aus.1", "A-League", "Australia", 3, "liga"),
    ("chn.1", "Superliga", "China", 3, "liga"),
    ("concacaf.champions", "Concacaf Champions Cup", "Norteamérica", 3, "copa"),
    ("concacaf.leagues.cup", "Leagues Cup", "Norteamérica", 3, "copa"),
    ("uefa.champions_qual", "Champions (fase previa)", "Europa", 3, "copa"),
    # Conecta la fuerza de los distintos continentes entre sí
    ("fifa.cwc", "Mundial de Clubes", "Mundial", 3, "copa"),
]
# Resto de competiciones de ESPN (todo el mundo): se descubren solas y se guardan en datos/ligas_espn.json
ARCHIVO_LIGAS_ESPN = DATOS / "ligas_espn.json"
try:
    import json as _json
    _base = {c for c, *_ in LIGAS}
    for _l in _json.loads(ARCHIVO_LIGAS_ESPN.read_text(encoding="utf-8")).get("ligas", []):
        if _l["codigo"] not in _base:
            LIGAS.append((_l["codigo"], _l["nombre"], _l["pais"], 4, _l["tipo"]))
except Exception:
    pass
INFO_LIGA = {c: {"codigo": c, "nombre": n, "pais": p, "nivel": nv, "tipo": t} for c, n, p, nv, t in LIGAS}


def agregar_ligas(ligas):
    """Suma (sin reiniciar) las competiciones descubiertas en ESPN que aún no están en la lista."""
    for l in ligas:
        if l["codigo"] not in INFO_LIGA:
            LIGAS.append((l["codigo"], l["nombre"], l["pais"], 4, l["tipo"]))
            INFO_LIGA[l["codigo"]] = {"codigo": l["codigo"], "nombre": l["nombre"], "pais": l["pais"], "nivel": 4,
                                      "tipo": l["tipo"]}

# Copas que pueden provocar rotaciones en la liga local (D24.6)
COPAS_PRIORITARIAS = {"uefa.champions", "uefa.europa", "uefa.europa.conf",
                      "conmebol.libertadores", "conmebol.sudamericana", "concacaf.champions"}

# --- Ventanas de datos ---
MESES_HISTORIA = 26          # meses de resultados que se descargan
DIAS_PROXIMOS = 10           # días hacia adelante que se predicen
DIAS_RESULTADOS = 4          # días hacia atrás que se muestran como resultados

# --- Modelo ---
VIDAS_MEDIAS_CANDIDATAS = [120, 200, 300, 450]   # días; se elige la mejor por validación
VIDA_MEDIA_POR_DEFECTO = 200
PRIOR_EQUIPO = 5.0        # fuerza (en goles esperados) de la regresión a la media de su liga
PRIOR_LOCALIA_EQUIPO = 25.0
PRIOR_LOCALIA_COMP = 60.0
SIMULACIONES_INTERVALO = 400
SEMANAS_VALIDACION = 52      # también es la muestra de la prueba de apuestas con cuotas reales

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PrediccionesFutbol/1.0"
