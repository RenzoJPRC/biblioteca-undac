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
* 📊 **Dashboard en Tiempo Real**: Métricas en vivo, contadores de aforo por sala de lectura y gráficas estadísticas de concurrencia diaria/mensual.
* ⚡ **Carga Masiva Asíncrona**: Importación de padrones masivos en formato Excel (`.xlsx`, `.xls`) procesados en segundo plano mediante hilos de ejecución (*Background Threads*).
* 🛡️ **Seguridad y Control de Accesos (RBAC)**: Autenticación de administradores con roles segregados (*SuperAdmin*, *Supervisor*, *Consultor*), protección CSRF nativa y registros automáticos de auditoría (`AdminAuditLog`).
* 📶 **Resiliencia de Red y Servidor**: Configuración WSGI de alta concurrencia (`Waitress` con 16 hilos) y manejo de desconexiones temporales en terminales cliente.

---

## 🛠️ Tecnologías Utilizadas

* **Backend**: Python 3.10+, Flask, Waitress WSGI.
* **Base de Datos**: Microsoft SQL Server (vía `pyodbc` y Procedimientos Almacenados).
* **Frontend**: HTML5, CSS3 (Vanilla / TailwindCSS), JavaScript (ES6+), Jinja2.
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
│         Servidor WSGI Waitress (Multi-Thread 16)       │
│                  Aplicación Flask (app.py)             │
│  Middleware: CSRF, AuditLog, Control de Sesiones       │
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
├── requirements.txt           # Dependencias de Python
├── example.env.txt            # Plantilla de variables de entorno de ejemplo
├── routes/                    # Módulos de rutas segregadas por Blueprints
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
├── static/                    # Archivos estáticos (CSS, JS, sonidos de escáner)
└── templates/                 # Plantillas HTML en Jinja2
```

---

## ⚙️ Guía de Instalación y Despliegue

### 1. Requisitos Previos

* Python 3.10 o superior instalado.
* Microsoft SQL Server 2017 o superior.
* `ODBC Driver 17 for SQL Server` instalado en el sistema operativo.

### 2. Clonar el Repositorio

```bash
git clone https://github.com/RenzoJPRC/biblioteca-ingreso-undac.git
cd biblioteca-ingreso-undac
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
