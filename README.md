# 📚 Sistema de Control de Accesos y Registro Bibliotecario - UNDAC

Un sistema web modular, de alto rendimiento y grado de producción diseñado para el **Control de Accesos, Registro de Ingresos y Gestión de Salas** en la Biblioteca Central y Filiales de la **Universidad Nacional Daniel Alcides Carrión (UNDAC)**.

El sistema está optimizado para funcionar en entornos de red local (intranet) conectado a lectores de código de barras/QR y molinetes, atendiendo múltiples accesos simultáneos sin latencia.

---

## 🚀 Características Principales

* 🎯 **Escaneo Ultrarrápido**: Procesamiento de carnets institucionales y DNIs en milisegundos mediante escáneres de código de barras o QR.
* ⏱️ **Lógica de 6 Bloques de Horario**: Restricción inteligente de accesos repetidos por bloques de 2 horas (08:00-10:00, 10:00-12:00, 12:00-02:00, 02:00-04:00, 04:00-06:00, 06:00-08:45) gestionada atómicamente por Stored Procedures en SQL Server.
* 👥 **Identificación Polimórfica Integrada**: Soporta 5 tipos de entidades institucionales:
  * 🎓 Alumnos
  * 🎓 Egresados
  * 👨‍🏫 Docentes
  * 💼 Personal Administrativo
  * 🏛️ Visitantes Externos
* 🗺️ **Geolocalización y Mapas Interactivos (Google Maps & REST APIs)**: Visualización interactiva de las 6 bibliotecas y filiales de la UNDAC (Central Cerro de Pasco, Tarma, La Merced, Oxapampa, Yanahuanca, Paucartambo), geocodificación en tiempo real, trazado de rutas vehiculares por pistas y consulta de clima con Open-Meteo.
* ☁️ **Despliegue Cloud en Render.com + Dominio Propio**: Configuración lista de producción WSGI Gunicorn (`Procfile`, `render.yaml`) enlazada al dominio personalizado `https://bibliotecarenzo.xyz`.

---

## 🛠️ Tecnologías Utilizadas

* **Backend**: Python 3.10+, Flask, Waitress WSGI (Local), Gunicorn WSGI (Cloud Render).
* **Geolocalización & APIs REST**: Google Maps JavaScript API v3, OpenStreetMap Nominatim REST API, OSRM Routing API, Open-Meteo Weather REST API.
* **Base de Datos**: Microsoft SQL Server (vía `pyodbc` y Procedimientos Almacenados).
* **Frontend**: HTML5, CSS3 (Vanilla / TailwindCSS), JavaScript (ES6+), Leaflet fallback, Jinja2.
* **Procesamiento de Datos**: Pandas, OpenPyXL.
* **Iconografía**: Phosphor Icons.

---

## 🏗️ Arquitectura del Sistema

```text
┌────────────────────────────────────────────────────────┐
│         Terminales Cliente / Lectores de Barras-QR     │
│         (Salas de Lectura, Cómputo, Tesis, Eventos)     │
└───────────────────────────┬────────────────────────────┘
                            │ (Petición HTTP POST en Intranet)
┌───────────────────────────▼────────────────────────────┐
│         Servidor WSGI Waitress / Gunicorn (Render)     │
│                  Aplicación Flask (app.py)             │
│  Blueprints: Access Control, REST APIs, AuditLog, Maps │
└───────────────────────────┬────────────────────────────┘
                            │ (ODBC Driver 17 / Timeout 15s)
┌───────────────────────────▼────────────────────────────┐
│            Microsoft SQL Server (BibliotecaUNDAC)      │
│  SP: [sp_RegistrarIngreso] + Tablas de Entidades       │
└────────────────────────────────────────────────────────┘
```

---

## 📂 Estructura del Proyecto

```text
SistemaBiblioteca/
├── app.py                     # Punto de entrada principal y configuración de Flask/Waitress
├── app.bat                    # Script de arranque en un solo clic para Windows Server
├── db.py                      # Conector centralizado a Microsoft SQL Server (pyodbc)
├── Procfile                   # Configuración de proceso Web para Render.com (Gunicorn)
├── render.yaml                # Blueprint 1-click deployment para Render.com
├── requirements.txt           # Dependencias de Python de producción
├── README.md                  # Documentación principal del sistema integral
├── README_API_MAPS.md         # Documentación dedicada del Módulo REST API de Maps
├── URL_PUBLICA.txt            # Dominio de producción (https://bibliotecarenzo.xyz)
├── modulo_google_maps_undac/  # Módulo autónomo independiente para evaluación académica
├── modulo_google_maps_undac.zip # Paquete zip autocontenido comprimido
├── routes/                    # Módulos de rutas segregadas por Blueprints
│   ├── api_external.py        # REST API de Geolocalización, Maps, Geocoding y Clima
│   ├── ingreso.py             # Control de accesos y escaneo de barras
│   ├── visitantes.py          # Gestión de visitantes externos
│   ├── admin_auth.py          # Autenticación y control de login administrativo
│   ├── admin_dashboard.py     # Panel de control y estadísticas en tiempo real
│   ├── admin_carnets.py       # Padrón de Alumnos y carnets
│   ├── admin_egresados.py     # Padrón de Egresados
│   ├── admin_docentes.py      # Padrón de Docentes
│   ├── admin_personal.py      # Padrón de Personal Administrativo
│   ├── admin_reportes.py      # Exportación de reportes a Excel
│   ├── admin_eventos.py       # Asistencia y control de eventos institucionales
│   ├── admin_salas.py         # Configuración de salas de lectura
│   └── admin_auditoria.py     # Logs de auditoría de acciones de usuarios
├── utils/                     # Consultas SQL aisladas, validaciones y gestor de tareas
│   ├── queries_ingreso.py     # Ejecución de Stored Procedures de escaneo
│   ├── validaciones.py        # Validación de DNIs y duplicados
│   └── task_manager.py       # Gestor de tareas asíncronas en segundo plano
├── static/                    # Archivos estáticos (CSS, JS, sonidos de escáner, logos)
└── templates/                 # Plantillas HTML en Jinja2 (mapa_filiales.html, etc.)
```

---

## ⚙️ Guía de Instalación y Despliegue

### 1. Requisitos Previos

* Python 3.10 o superior instalado.
* Microsoft SQL Server 2017 o superior (para funcionalidad completa de accesos).
* `ODBC Driver 17 for SQL Server` instalado en el sistema operativo.

### 2. Clonar el Repositorio

```bash
git clone https://github.com/RenzoJPRC/biblioteca-undac.git
cd biblioteca-undac
```

### 3. Crear Entorno Virtual e Instalar Dependencias

En Windows PowerShell o Consola:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto tomando como referencia `example.env.txt`:

```env
FLASK_APP=app.py
FLASK_ENV=production

# Clave secreta para sesiones (Generar una cadena aleatoria segura)
SECRET_KEY=tu_clave_secreta_criptografica_64_caracteres

# Configuración de Base de Datos SQL Server
DB_DRIVER={ODBC Driver 17 for SQL Server}
DB_SERVER=NOMBRE_DE_TU_SERVIDOR\SQLEXPRESS
DB_DATABASE=BibliotecaUNDAC
DB_TRUSTED_CONNECTION=yes
PORT=5000
```

### 5. Configurar la Base de Datos en SQL Server

Ejecuta el script SQL maestro en **SQL Server Management Studio (SSMS)** para crear la base de datos, tablas, índices y el procedimiento almacenado principal:

```sql
-- Ejecutar el script contenido en BD_BibliotecaUNDAC_Final.sql o la plantilla equivalente.
```

### 6. Iniciar el Servidor de Producción

Puedes iniciar el servidor directamente con Python:

```bash
python app.py
```

O en servidores Windows, haciendo doble clic en el archivo optimizado:

```cmd
app.bat
```

El servidor iniciará en `http://0.0.0.0:5000` y estará accesible para todas las PCs cliente dentro de la red local.

---

## 🔒 Seguridad y Privacidad

* Las contraseñas de los usuarios administrativos utilizan algoritmos de hashing criptográfico estricto (`scrypt` / `pbkdf2`).
* Todas las operaciones mutables (`POST`, `PUT`, `DELETE`) están protegidas con tokens **CSRF**.
* Los archivos temporales, respaldos y credenciales reales de la base de datos están explícitamente excluidos en `.gitignore` para cumplir con las normativas de protección de datos personales de la universidad.

---

## 📄 Licencia

Desarrollado para la **Universidad Nacional Daniel Alcides Carrión (UNDAC)**. Todos los derechos reservados.
