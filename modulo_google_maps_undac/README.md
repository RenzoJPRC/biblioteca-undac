# 🗺️ Módulo Autónomo REST API: Google Maps, Geocoding & Geolocalización UNDAC

**Asignatura:** Automatización de Procesos  
**Docente:** JOSE LUIS SOSA SANCHEZ  
**Estudiante:** Renzo Juan Pablo Rojas Castillo  
**Proyecto / Módulo:** Módulo de Geolocalización, Geocodificación y Rutas con APIs REST Externas  

---

## 📌 1. Descripción del Módulo

Este es un **Módulo Independiente, Autocontenido y Reutilizable** desarrollado en **Python (Flask)**. Ofrece una solución completa de geolocalización, trazado de rutas vehiculares y geocodificación para la Red de Bibliotecas de la Universidad Nacional Daniel Alcides Carrión (UNDAC).

### 🚀 Funcionalidades Principales:
1. 📍 **Geolocalización Institucional de Alta Precisión**:
   - **Biblioteca Central UNDAC (Cerro de Pasco)**: `-10.668115, -76.253753` (San Juan Pampa).
   - **Filial Tarma**: `-11.41556, -75.7092` (Sacsamarca).
   - **Filial La Merced**: `-11.074661, -75.335492` (Av. Fray Dionisio Ortiz 240).
   - **Filial Oxapampa**: `-10.5941, -75.3844` (Ciudad Universitaria).
   - **Filial Yanahuanca**: `-10.489627, -76.508021` (Chamayog s/n).
   - **Filial Paucartambo**: `-10.7704, -75.8150` (Av. Universitaria s/n).

2. 🔍 **Geocodificación REST API Externa (`/api/geocodificar?q=...`)**:
   - Convierte cualquier dirección o nombre de lugar tipeado por el usuario en coordenadas `lat` y `lng` consumiendo en tiempo real la API REST pública de Geocodificación.

3. 🌦️ **Clima en Tiempo Real por Campus (`/api/clima_coordenada?lat=...&lng=...`)**:
   - Consulta la API REST pública de Open-Meteo para mostrar la temperatura y clima actual de la filial seleccionada.

4. 🛣️ **Trazado de Rutas por Pistas y Calles (OSRM / Google Directions)**:
   - Traza la ruta vehicular siguiendo el camino real de las calles y calculando la distancia en kilómetros y minutos estimados de viaje.

5. 🛰️ **Cambiador de Capas (Mapa Calles, Satélite HD, Terreno)**:
   - Permite alternar entre mapa vectorial, fotografía satelital en alta definición y relieve topográfico desde la barra superior.

---

## ⚙️ 2. Guía de Instalación y Ejecución Local (Paso a Paso)

Para ejecutar este módulo en su computadora local, siga estos 3 sencillos pasos:

### Paso 1: Descomprimir el Proyecto
Extraiga el contenido del archivo comprimido `.zip` en cualquier carpeta de su equipo.

### Paso 2: Instalar Dependencias
Abra la consola / terminal dentro de la carpeta del proyecto y ejecute:
```bash
pip install -r requirements.txt
```

### Paso 3: Iniciar el Servidor Local
Ejecute el comando principal de inicio:
```bash
python app.py
```

¡Listo! Abra su navegador e ingrese a la siguiente dirección:
👉 **`http://127.0.0.1:5000/mapa`**

---

## 📡 3. Endpoints REST API Disponibles

| Endpoint | Método | Descripción | Ejemplo de Uso |
| :--- | :---: | :--- | :--- |
| `/mapa` | `GET` | Vista web interactiva con el mapa de filiales UNDAC. | `http://127.0.0.1:5000/mapa` |
| `/api/ubicaciones_undac` | `GET` | Colección JSON de las 6 filiales con sus coordenadas y aforos. | `http://127.0.0.1:5000/api/ubicaciones_undac` |
| `/api/geocodificar` | `GET` | Geocodificación de direcciones a coordenadas (`q=nombre`). | `http://127.0.0.1:5000/api/geocodificar?q=Tarma` |
| `/api/clima_coordenada` | `GET` | Clima en tiempo real por coordenadas `lat` y `lng`. | `http://127.0.0.1:5000/api/clima_coordenada?lat=-10.668115&lng=-76.253753` |

---

## 🧩 4. Integración a Otro Sistema de Información

Este módulo fue diseñado bajo la arquitectura modular de **Blueprints de Flask**, lo que permite integrarlo fácilmente a cualquier otra aplicación web en 2 líneas de código:

```python
from routes.api_external import api_external_bp

# Registrar el módulo en su aplicación Flask principal:
app.register_blueprint(api_external_bp)
```

---

&copy; 2026 Renzo Juan Pablo Rojas Castillo - Todos los derechos reservados.
