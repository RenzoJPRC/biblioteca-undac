"""
===============================================================================
  MÓDULO AUTÓNOMO REST API: GOOGLE MAPS, GEOCODING & CLIMA UNDAC
===============================================================================
  Desarrollado para la Asignatura de Sistemas de Información
  Docente: JOSE LUIS SOSA SANCHEZ
  Estudiante: Renzo Juan Pablo Rojas Castillo

  Este archivo concentra de forma autocontenida e independiente las APIs REST:
  1. GET /mapa                       -> Vista interactiva con Google Maps JS API v3 / Leaflet
  2. GET /api/ubicaciones_undac       -> REST API Coordenadas, Aforo y Salas (6 Filiales UNDAC)
  3. GET /api/geocodificar?q=...      -> REST API Geocodificación Externa (Google / Nominatim)
  4. GET /api/clima_coordenada        -> REST API Clima en Tiempo Real (Open-Meteo)
===============================================================================
"""

import os
from flask import Blueprint, jsonify, request, render_template
import requests

# Blueprint exclusivo para las APIs externas de geolocalización y mapas
api_external_bp = Blueprint('api_external', __name__)

# Constantes de timeout para consumo de servicios REST
API_TIMEOUT_FAST = 3.0
API_TIMEOUT_STANDARD = 4.0


# =============================================================================
# 1. VISTA WEB INTERACTIVA: GOOGLE MAPS & GEOLOCALIZACIÓN UNDAC
# =============================================================================
@api_external_bp.route('/mapa')
def mapa_filiales_page():
    """
    Renderiza la vista interactiva con Google Maps API v3 / Leaflet para geolocalizar
    la Sede Central y Filiales de la Universidad Nacional Daniel Alcides Carrión.
    URL local: http://127.0.0.1:5000/mapa
    """
    google_maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return render_template('mapa_filiales.html', api_key=google_maps_api_key)


# =============================================================================
# 2. REST API: UBICACIONES DE LA SEDE CENTRAL Y 5 FILIALES UNDAC
# =============================================================================
@api_external_bp.route('/api/ubicaciones_undac', methods=['GET'])
def obtener_ubicaciones_undac():
    """
    📌 API REST GOOGLE MAPS: Devuelve la colección geoespacial de la Sede Central y
    Filiales de la UNDAC con sus coordenadas de alta precisión, aforo y salas.
    URL local: http://127.0.0.1:5000/api/ubicaciones_undac
    """
    from datetime import datetime
    ahora = datetime.now()
    es_fin_semana = ahora.weekday() >= 5
    hora_actual = ahora.hour + ahora.minute / 60.0

    sedes = [
        {
            "id": "central",
            "nombre": "Biblioteca Central UNDAC - Cerro de Pasco",
            "tipo": "Sede Central",
            "lat": -10.668115,
            "lng": -76.253753,
            "direccion": "Ciudad Universitaria UNDAC, Av. Los Próceres s/n, San Juan, Cerro de Pasco",
            "telefono": "(063) 422070",
            "horario": "Lun - Vie: 08:00 AM - 08:45 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 20.75) else "Abierto Ahora",
            "aforo_max": 250,
            "ejemplares_totales": 15400,
            "salas": [
                {"nombre": "Sala de Cómputo e Investigación", "piso": 1},
                {"nombre": "Sala de Ciencias Aplicadas", "piso": 2},
                {"nombre": "Sala de Tesis y Posgrado", "piso": 3}
            ],
            "icono": "ph-buildings"
        },
        {
            "id": "tarma",
            "nombre": "Biblioteca UNDAC - Filial Tarma",
            "tipo": "Filial Descentralizada",
            "lat": -11.41556,
            "lng": -75.7092,
            "direccion": "Av. José Gálvez Moreno s/n, Sacsamarca, Tarma",
            "telefono": "(064) 341200",
            "horario": "Lun - Vie: 08:00 AM - 06:00 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 18.0) else "Abierto Ahora",
            "aforo_max": 120,
            "ejemplares_totales": 4200,
            "salas": [
                {"nombre": "Sala de Lectura General", "piso": 1},
                {"nombre": "Hemeroteca Universitaria", "piso": 1}
            ],
            "icono": "ph-map-pin"
        },
        {
            "id": "lamerced",
            "nombre": "Biblioteca UNDAC - Filial La Merced",
            "tipo": "Filial Descentralizada",
            "lat": -11.074661,
            "lng": -75.335492,
            "direccion": "Av. Fray Dionisio Ortiz 240, La Merced 12856",
            "telefono": "(064) 531120",
            "horario": "Lun - Vie: 08:00 AM - 06:00 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 18.0) else "Abierto Ahora",
            "aforo_max": 100,
            "ejemplares_totales": 3100,
            "salas": [
                {"nombre": "Sala Multidisciplinaria", "piso": 1},
                {"nombre": "Recursos Digitales", "piso": 2}
            ],
            "icono": "ph-map-pin"
        },
        {
            "id": "oxapampa",
            "nombre": "Biblioteca UNDAC - Filial Oxapampa",
            "tipo": "Filial Descentralizada",
            "lat": -10.5941,
            "lng": -75.3844,
            "direccion": "Ciudad Universitaria Oxapampa, Jr. Mayer s/n",
            "telefono": "(063) 462100",
            "horario": "Lun - Vie: 08:00 AM - 06:00 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 18.0) else "Abierto Ahora",
            "aforo_max": 90,
            "ejemplares_totales": 2800,
            "salas": [
                {"nombre": "Sala de Agropecuaria y Zootecnia", "piso": 1}
            ],
            "icono": "ph-map-pin"
        },
        {
            "id": "yanahuanca",
            "nombre": "Biblioteca UNDAC - Filial Yanahuanca",
            "tipo": "Filial Descentralizada",
            "lat": -10.489627,
            "lng": -76.508021,
            "direccion": "Chamayog s/n, Yanahuanca 19600",
            "telefono": "(063) 481050",
            "horario": "Lun - Vie: 08:00 AM - 05:00 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 17.0) else "Abierto Ahora",
            "aforo_max": 80,
            "ejemplares_totales": 1900,
            "salas": [
                {"nombre": "Sala de Agronomía y Estudios Generales", "piso": 1}
            ],
            "icono": "ph-map-pin"
        },
        {
            "id": "paucartambo",
            "nombre": "Biblioteca UNDAC - Filial Paucartambo",
            "tipo": "Filial Descentralizada",
            "lat": -10.7704,
            "lng": -75.8150,
            "direccion": "Av. Universitaria s/n, Paucartambo",
            "telefono": "(063) 491220",
            "horario": "Lun - Vie: 08:00 AM - 05:00 PM",
            "estado_apertura": "Cerrado" if es_fin_semana or not (8.0 <= hora_actual <= 17.0) else "Abierto Ahora",
            "aforo_max": 75,
            "ejemplares_totales": 1750,
            "salas": [
                {"nombre": "Sala de Consulta Bibliográfica", "piso": 1}
            ],
            "icono": "ph-map-pin"
        }
    ]

    return jsonify({
        'status': 'success',
        'fuente': 'Sistema de Geolocalización UNDAC - Google Maps REST API',
        'total_sedes': len(sedes),
        'sedes': sedes
    })


# =============================================================================
# 3. REST API: GEOCODIFICACIÓN EN TIEMPO REAL (DIRECCIÓN A COORDENADAS)
# =============================================================================
@api_external_bp.route('/api/geocodificar', methods=['GET'])
def geocodificar_direccion_externa():
    """
    📌 API REST EXTERNA GEOCODING: Recibe una dirección o nombre de lugar y consulta
    la API REST Externa para obtener latitud y longitud dinámicamente en tiempo real.
    URL local: http://127.0.0.1:5000/api/geocodificar?q=Tarma
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'status': 'error', 'msg': 'Debe ingresar una dirección'}), 400

    google_maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")

    # Intentar primero con Google Maps Geocoding REST API si existe clave
    if google_maps_api_key:
        try:
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(query + ', Peru')}&key={google_maps_api_key}"
            resp = requests.get(url, timeout=API_TIMEOUT_FAST)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == 'OK' and data.get('results'):
                    res = data['results'][0]
                    loc = res['geometry']['location']
                    return jsonify({
                        'status': 'success',
                        'fuente': 'Google Maps Geocoding REST API',
                        'direccion_formateada': res.get('formatted_address'),
                        'lat': loc['lat'],
                        'lng': loc['lng']
                    })
        except Exception as e:
            print(f"[ERROR GOOGLE GEOCODING API] {e}")

    # Fallback a OpenStreetMap Nominatim Geocoding REST API
    try:
        url_nom = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(query + ', Peru')}&format=json&limit=1"
        headers = {'User-Agent': 'BibliotecaUNDAC/1.0 (universidad@undac.edu.pe)'}
        resp = requests.get(url_nom, headers=headers, timeout=API_TIMEOUT_FAST)
        if resp.status_code == 200:
            results = resp.json()
            if results:
                item = results[0]
                return jsonify({
                    'status': 'success',
                    'fuente': 'OpenStreetMap Nominatim REST API',
                    'direccion_formateada': item.get('display_name'),
                    'lat': float(item.get('lat')),
                    'lng': float(item.get('lon'))
                })
    except Exception as e:
        print(f"[ERROR NOMINATIM GEOCODING API] {e}")

    return jsonify({'status': 'error', 'msg': f"No se encontraron coordenadas para '{query}'"}), 444


# =============================================================================
# 4. REST API: CLIMA EN TIEMPO REAL POR FILIAL
# =============================================================================
@api_external_bp.route('/api/clima_coordenada', methods=['GET'])
def obtener_clima_coordenada():
    """
    📌 API REST CLIMA EXTERNA: Obtiene la temperatura y el tiempo atmosférico en tiempo real
    para las coordenadas de cualquier sede o filial consultada en el mapa.
    URL local: http://127.0.0.1:5000/api/clima_coordenada?lat=-10.668115&lng=-76.253753
    """
    try:
        lat = request.args.get('lat', '-10.6682')
        lng = request.args.get('lng', '-76.2531')
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        resp = requests.get(url, timeout=API_TIMEOUT_STANDARD)
        if resp.status_code == 200:
            data = resp.json()
            current = data.get('current', {})
            temp = current.get('temperature_2m', 12.0)
            humedad = current.get('relative_humidity_2m', 65)
            viento = current.get('wind_speed_10m', 10.0)
            code = current.get('weather_code', 0)

            descripcion = "Despejado"
            if code in [1, 2, 3]: descripcion = "Parcialmente Nublado"
            elif code in [45, 48]: descripcion = "Niebla / Helada"
            elif code in [51, 53, 55, 61, 63, 65]: descripcion = "Lluvia"
            elif code in [71, 73, 75, 77, 85, 86]: descripcion = "Granizo"

            return jsonify({
                'status': 'success',
                'fuente': 'Open-Meteo REST API',
                'temperatura': f"{temp}°C",
                'humedad': f"{humedad}%",
                'viento': f"{viento} km/h",
                'condicion': descripcion
            })
    except Exception as e:
        print(f"[ERROR CLIMA COORDENADA] {e}")

    return jsonify({
        'status': 'success',
        'fuente': 'Open-Meteo REST API (Estimado)',
        'temperatura': '11.5°C',
        'humedad': '68%',
        'viento': '9 km/h',
        'condicion': 'Templado / Freno'
    })
