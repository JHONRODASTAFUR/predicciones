"""Ubicación, altitud y clima de los estadios con Open-Meteo (gratis, sin cuenta).

Solo se envía el nombre de la ciudad (para ubicarla) y sus coordenadas (para el clima).
"""
import datetime as dt
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .config import ARCHIVO_GEO
from .datos import http_json

URL_GEO = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=10&language=es&format=json{pais}"
URL_CLIMA = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
             "&hourly=temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,"
             "wind_speed_10m,weather_code&timezone=UTC&forecast_days=9")

PAISES = {
    "England": "GB", "Scotland": "GB", "Wales": "GB", "Northern Ireland": "GB", "United Kingdom": "GB",
    "Spain": "ES", "France": "FR", "Italy": "IT", "Germany": "DE", "Portugal": "PT", "Netherlands": "NL",
    "Belgium": "BE", "Turkey": "TR", "Türkiye": "TR", "Greece": "GR", "Austria": "AT", "Denmark": "DK",
    "Norway": "NO", "Sweden": "SE", "Russia": "RU", "Switzerland": "CH", "Czech Republic": "CZ", "Czechia": "CZ",
    "Poland": "PL", "Croatia": "HR", "Serbia": "RS", "Ukraine": "UA", "Ireland": "IE", "Republic of Ireland": "IE",
    "Israel": "IL", "Cyprus": "CY", "Hungary": "HU", "Romania": "RO", "Bulgaria": "BG", "Slovakia": "SK",
    "Slovenia": "SI", "Azerbaijan": "AZ", "Kazakhstan": "KZ", "Finland": "FI", "Iceland": "IS", "Moldova": "MD",
    "Belarus": "BY", "Georgia": "GE", "Armenia": "AM", "Albania": "AL", "Kosovo": "XK", "North Macedonia": "MK",
    "Bosnia and Herzegovina": "BA", "Bosnia-Herzegovina": "BA", "Montenegro": "ME", "Lithuania": "LT",
    "Latvia": "LV", "Estonia": "EE", "Luxembourg": "LU", "Malta": "MT", "Gibraltar": "GI", "Andorra": "AD",
    "San Marino": "SM", "Faroe Islands": "FO", "Liechtenstein": "LI",
    "Peru": "PE", "Argentina": "AR", "Brazil": "BR", "Colombia": "CO", "Chile": "CL", "Uruguay": "UY",
    "Ecuador": "EC", "Paraguay": "PY", "Bolivia": "BO", "Venezuela": "VE",
    "USA": "US", "United States": "US", "Canada": "CA", "Mexico": "MX", "Costa Rica": "CR", "Honduras": "HN",
    "Guatemala": "GT", "Panama": "PA", "El Salvador": "SV", "Jamaica": "JM", "Nicaragua": "NI",
    "Trinidad and Tobago": "TT", "Haiti": "HT", "Dominican Republic": "DO", "Suriname": "SR",
    "Saudi Arabia": "SA", "Japan": "JP", "Australia": "AU", "China": "CN", "China PR": "CN", "Qatar": "QA",
    "United Arab Emirates": "AE", "UAE": "AE", "South Korea": "KR", "Korea Republic": "KR",
}


def _cargar():
    try:
        return json.loads(ARCHIVO_GEO.read_text(encoding="utf-8"))
    except Exception:
        return {"lugares": {}, "clima": {}}


def _guardar(g):
    tmp = ARCHIVO_GEO.with_suffix(".tmp")
    tmp.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ARCHIVO_GEO)


def clave(ciudad, pais):
    return f"{(ciudad or '').strip()}|{(pais or '').strip()}"


def _ubicar(ciudad, pais):
    nombre = ciudad.split(",")[0].strip()
    if not nombre:
        return None
    cc = PAISES.get(pais)
    for filtro in ([f"&countryCode={cc}"] if cc else []) + [""]:
        try:
            d = http_json(URL_GEO.format(q=urllib.parse.quote(nombre), pais=filtro), intentos=2)
        except Exception:
            return "error"
        res = d.get("results") or []
        if cc and filtro == "":
            res = [r for r in res if r.get("country_code") == cc] or res
        if res:
            r = max(res[:10], key=lambda r: (r.get("population") or 0))
            return {"lat": r.get("latitude"), "lon": r.get("longitude"), "alt": r.get("elevation"),
                    "tz": r.get("timezone"), "nombre": r.get("name")}
    return None


def ubicar_ciudades(pares, maximo=350):
    """Geocodifica ciudades nuevas (se guardan para siempre). Devuelve el diccionario de lugares."""
    g = _cargar()
    lug = g.setdefault("lugares", {})
    hoy = dt.date.today().isoformat()
    pendientes = []
    for ciudad, pais in pares:
        k = clave(ciudad, pais)
        if not ciudad:
            continue
        v = lug.get(k)
        if v is None or (isinstance(v, dict) and v.get("fallo") and v.get("fecha", "") < (dt.date.today() - dt.timedelta(days=30)).isoformat()):
            pendientes.append((ciudad, pais))
    pendientes = list(dict.fromkeys(pendientes))[:maximo]

    def una(p):
        return p, _ubicar(*p)

    with ThreadPoolExecutor(4) as ex:
        for (ciudad, pais), r in ex.map(una, pendientes):
            if r == "error":
                continue
            lug[clave(ciudad, pais)] = r if r else {"fallo": True, "fecha": hoy}
    _guardar(g)
    return {k: v for k, v in lug.items() if isinstance(v, dict) and not v.get("fallo")}


def clima_partidos(partidos, lugares):
    """Pronóstico a la hora del partido para los encuentros de los próximos 8 días."""
    ahora = dt.datetime.now(dt.timezone.utc)
    puntos = {}
    for p in partidos:
        lu = lugares.get(clave(p.get("ciudad"), p.get("pais")))
        if not lu or lu.get("lat") is None:
            continue
        ko = dt.datetime.fromisoformat(p["fecha"].replace("Z", "+00:00"))
        if not (ahora - dt.timedelta(hours=3) <= ko <= ahora + dt.timedelta(days=8)):
            continue
        k = (round(lu["lat"], 2), round(lu["lon"], 2))
        puntos.setdefault(k, []).append((p, ko))
    claves = list(puntos)
    resultado = {}

    def lote(ks):
        try:
            d = http_json(URL_CLIMA.format(lat=",".join(str(k[0]) for k in ks), lon=",".join(str(k[1]) for k in ks)),
                          intentos=2)
        except Exception:
            return []
        return list(zip(ks, d if isinstance(d, list) else [d]))

    lotes = [claves[i:i + 40] for i in range(0, len(claves), 40)]
    with ThreadPoolExecutor(3) as ex:
        for res in ex.map(lote, lotes):
            for k, datos in res:
                hr = (datos or {}).get("hourly") or {}
                horas = hr.get("time") or []
                indice = {t: i for i, t in enumerate(horas)}
                for p, ko in puntos[k]:
                    t = ko.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                    i = indice.get(t)
                    if i is None:
                        continue

                    def val(n):
                        arr = hr.get(n) or []
                        return arr[i] if i < len(arr) else None

                    resultado[p["id"]] = {
                        "temp": val("temperature_2m"), "humedad": val("relative_humidity_2m"),
                        "lluvia_mm": val("precipitation"), "prob_lluvia": val("precipitation_probability"),
                        "viento": val("wind_speed_10m"), "codigo": val("weather_code"),
                    }
    return resultado
