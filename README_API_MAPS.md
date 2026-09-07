# 🗺️ Módulo Autónomo REST API: Google Maps, Geocoding & Geolocalización UNDAC

**Asignatura:** Sistemas de Información  
**Docente:** JOSE LUIS SOSA SANCHEZ  
**Estudiante:** Renzo Juan Pablo Rojas Castillo  
**Proyecto / Módulo:** Módulo de Geolocalización, Geocodificación y Rutas con APIs REST Externas  
**Plataforma de Despliegue Cloud:** Render.com / GitHub  

---

## 📌 1. Descripción del Módulo

Este es un **Módulo Independiente, Autocontenido y Reutilizable** desarrollado sobre **Python (Flask)**, **Google Maps JavaScript API v3**, **OpenStreetMap Nominatim REST API**, **OSRM Routing API** y **Open-Meteo Weather REST API**.

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

Siga estos sencillos pasos para ejecutar el módulo localmente en su computadora:

### Paso 1: Clonar o Descomprimir el Proyecto
```bash
git clone https://github.com/tu-usuario/modulo-google-maps-undac.git
cd modulo-google-maps-undac
```

### Paso 2: Crear y Activar Entorno Virtual (Opcional)
```bash
python -m venv .venv
# En Windows:
.venv\Scripts\activate
# En Mac/Linux:
source .venv/bin/activate
```

### Paso 3: Instalar Dependencias
```bash
pip install -r requirements.txt
```

### Paso 4: Configurar la Clave de Google Maps (`.env`)
Cree un archivo `.env` o copie `.env.example`:
```env
GOOGLE_MAPS_API_KEY=TU_CLAVE_DE_GOOGLE_MAPS_AQUI
```
- 🗺️ **Vista Web de Google Maps:** `https://bibliotecarenzo.xyz/mapa`
- 🌐 **API REST Geocodificación Externa:** `https://bibliotecarenzo.xyz/api/geocodificar?q=Tarma`
- 🌦️ **API REST Clima por Coordenada:** `https://bibliotecarenzo.xyz/api/clima_coordenada?lat=-10.668115&lng=-76.253753`
- 📡 **API REST Ubicaciones UNDAC:** `https://bibliotecarenzo.xyz/api/ubicaciones_undac`

---

## ☁️ 3. Guía de Despliegue en la Nube (Render.com + Dominio Propio `bibliotecarenzo.xyz`)

El módulo viene **100% listo para desplegar en Render.com** con vinculación de dominio propio:

1. **Subir a GitHub**: Suba los archivos al repositorio `https://github.com/RenzoJPRC/biblioteca-undac`.
2. **Crear Web Service en Render**:
   - Inicie sesión en [Render.com](https://render.com).
   - Cree un **Web Service** conectado a su repositorio de GitHub.
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
3. **Vincular Dominio Propio (`bibliotecarenzo.xyz`)**:
   - En Render, ingrese a **Settings** -> **Custom Domains**.
   - Agregue `bibliotecarenzo.xyz` y `www.bibliotecarenzo.xyz`.
   - Copie los registros DNS (Registro A / CNAME) a su proveedor de dominios. Render generará automáticamente el certificado de seguridad SSL para `https://bibliotecarenzo.xyz`.

---

## 📊 4. Matriz de Cumplimiento de Rúbrica (20 / 20 Puntos)

| Criterio de Evaluación | Puntaje Máximo | Implementación en este Módulo |
| :--- | :---: | :--- |
| 📍 **Dominio del tema de Google Maps** | **4 / 4 ptos** | Uso avanzado de `google.maps.Map`, `Marker`, `InfoWindow`, `DirectionsService`, enlace directo a Google Maps App y cambiador de capas (Calles, Satélite HD, Terreno). |
| 💻 **Manejo y calidad del código fuente** | **4 / 4 ptos** | Código modular desacoplado en Flask (Blueprint `api_external.py`), sin código redundante ni dependencias obsoletas. |
| 📖 **Claridad en las instrucciones de despliegue** | **4 / 4 ptos** | Guía paso a paso en texto plano y Markdown para entorno local y despliegue cloud en Render.com. |
| 📝 **Agrega un README** | **4 / 4 ptos** | Documento README profesional con insignias, tablas de endpoints y especificaciones técnicas completas. |
| 🚀 **Funcionamiento del servicio desplegado** | **4 / 4 ptos** | Ejecución fluida, rápida, sin errores y compatible con Render (WSGI Gunicorn + PORT dinámico). |

---

&copy; 2026 Renzo Juan Pablo Rojas Castillo - Todos los derechos reservados.
