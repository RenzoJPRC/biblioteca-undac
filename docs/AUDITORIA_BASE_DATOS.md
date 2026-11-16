# GUÍA Y MANUAL TÉCNICO: CENTRO DE AUDITORÍA DE BASE DE DATOS Y SEGURIDAD
## Sistema Bibliotecario UNDAC
**Versión:** 2.0 (Edición Integral de Auditoría de Base de Datos y Cumplimiento Normativo)  
**Ambientes:** Producción (SmarterASP.NET SQL Server 2022) / Laboratorio (SQL Server Local - Caso Controlado)  
**Autor:** Auditoría Técnica de Sistemas e Infraestructura de Información

---

## 1. RESUMEN EJECUTIVO Y PROPÓSITO

El **Centro de Auditoría de Base de Datos y Seguridad** (`/admin/auditoria`) es una evolución técnica y metodológica sobre el módulo preexistente del Sistema Bibliotecario UNDAC. Su finalidad es dotar a la institución de una plataforma robusta, transparente y sustentada científicamente para ejecutar **auditorías de bases de datos académicas y forenses**, orientadas a evaluar la seguridad, confidencialidad, integridad y disponibilidad del repositorio institucional.

### Objetivos Clave:
1. **Evaluar la postura de seguridad de la base de datos:** Examinar de manera exhaustiva las identidades de SQL Server, roles, asignaciones de privilegios y configuraciones operativas.
2. **Brindar trazabilidad verificable:** Registrar tanto la actividad de la capa de aplicación (`AdminAuditLog`) como eventos a nivel de motor de datos (DML/DDL en entorno controlado).
3. **Garantizar la cadena de custodia forense:** Asignar identificadores únicos y hashes criptográficos SHA-256 a cada evidencia documental y técnica generada.
4. **Cumplir marcos de control reconocidos internacional y localmente:** Alinear las evaluaciones a CIS Microsoft SQL Server 2022 Benchmark, COBIT 2019 DSS05, Ley N.º 29733 (PDP Perú) y Ley N.º 30096 (Delitos Informáticos).
5. **Apoyar con rigor al auditor humano:** Mantener una rigurosa separación entre una hipótesis técnica de riesgo y un hallazgo formal confirmado, prohibiendo cualquier conclusión sesgada o automática del sistema.

---

## 2. ARQUITECTURA Y PRINCIPIO DE SEGREGACIÓN DE ENTORNOS

Para salvaguardar la integridad de la base de datos institucional en producción sin renunciar a la verificación empírica de controles avanzados, la arquitectura establece una **segregación estricta y obligatoria de dos ambientes**:

```
+---------------------------------------------------------------------------------------+
|                                    SISTEMA BIBLIOTECARIO UNDAC                        |
+-----------------------------------------------------------+---------------------------+
                                                            |
                       +------------------------------------+------------------------------------+
                       |                                                                         |
                       v                                                                         v
      +----------------------------------+                                     +----------------------------------+
      |      AMBIENTE DE PRODUCCIÓN      |                                     |      AMBIENTE DE LABORATORIO     |
      |   (SmarterASP.NET Cloud Server)  |                                     |    (Instancia SQL Server Local)  |
      +----------------------------------+                                     +----------------------------------+
      | • Modo EXCLUSIVO de Solo Lectura |                                     | • Pruebas de Auditoría DML       |
      | • Consultas SELECT a Vistas      |                                     |   (Triggers INSERT/UPDATE/DELETE)|
      |   de Catálogo (sys.*)            |                                     | • Pruebas de Auditoría DDL       |
      | • Lectura de AdminAuditLog       |                                     |   (Triggers CREATE/ALTER/DROP)   |
      | • Exportación de Evidencia (CSV) |                                     | • Ensayos de Restauración (.bak) |
      | • PROHIBIDO: INSERT/UPDATE/DROP  |                                     | • Verificación DBCC CHECKDB     |
      |   o DDL/DML de prueba en prod.   |                                     | • Pruebas destructivas seguras   |
      +----------------------------------+                                     +----------------------------------+
```

### 2.1 Principio Operativo en Producción (Bajo Riesgo)
En la base de datos de producción (`db_acdcfa_bibliotecaundac` alojada en SmarterASP.NET):
- **Permitido:** Sentencias `SELECT` de bajo impacto a tablas de auditoría y vistas de catálogo de base de datos (`sys.database_principals`, `sys.database_role_members`, `sys.database_permissions`).
- **Prohibido:** Cualquier modificación `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, pruebas de inyección o pruebas de estrés en caliente.

### 2.2 Principio Operativo en Laboratorio (Pruebas Controladas)
En el ambiente de laboratorio (instancia SQL Server local o contenedor Docker alimentado por una copia de respaldo `.bak` o script de esquema):
- Se ejecutan y validan los triggers de captura DML/DDL.
- Se realizan los ensayos de restauración y comprobación de páginas de disco (`RESTORE VERIFYONLY`, `DBCC CHECKDB`).
- **Advertencia metodológica visible en la interfaz:** Los porcentajes de captura DML/DDL corresponden única y exclusivamente al caso de prueba controlado ejecutado y no demuestran cobertura total en caliente del sistema de producción.

---

## 3. MARCOS DE REFERENCIA NORMATIVA Y ESTÁNDARES

El Centro de Auditoría evalúa la seguridad contrastando la evidencia empírica contra cuatro pilares normativos:

### 3.1 CIS Microsoft SQL Server 2022 Benchmark
- **§2 Autenticación y Autorización:**
  - *2.1 Control de Cuentas y Principales:* Descartar cuentas por defecto innecesarias, censo de usuarios y logins.
  - *2.2 Principio de Menor Privilegio (Roles):* Identificar miembros del rol privilegiado `db_owner` y evaluar la segregación funcional.
  - *2.8 Permisos Explícitos:* Auditoría de permisos directos `GRANT` o `DENY` sobre objetos de base de datos.
- **§3 Configuración de Cuentas:** Examen de políticas de contraseñas y desactivación de accesos anónimos (`guest`).
- **§5 Registro y Monitoreo (Logging):** Identificación de las especificaciones de auditoría a nivel de servidor (`SQL Server Audit Specification`). En hosting compartido se documenta la restricción técnica del proveedor como limitación justificada.

### 3.2 COBIT 2019 - Dominio DSS (Entregar, Servir y Dar Soporte)
- **DSS05.02 (Gestionar la Identidad y el Acceso Lógico):** Asignación y mantenimiento de privilegios en concordancia con los roles institucionales.
- **DSS05.04 (Gestionar la Seguridad del Entorno y Continuidad):** Disponibilidad, generación periódica de copias de respaldo y ensayos periódicos de restauración de la base de datos.
- **DSS05.07 (Monitorear la Infraestructura para Eventos de Seguridad):** Registro continuo y trazabilidad de operaciones críticas en bitácoras no volátiles.

### 3.3 Ley N.º 29733 (Ley de Protección de Datos Personales del Perú)
- **Principio de Seguridad:** Obligación de adoptar medidas técnicas, organizativas y legales para evitar la alteración, pérdida, tratamiento o acceso no autorizado a datos personales (como códigos de matrícula, DNI, nombres de estudiantes y docentes de la UNDAC).
- **Tratamiento Seguro de Datos Sensibles:** Enmascaramiento de tokens, claves y datos privados en las vistas y exportaciones de auditoría.

### 3.4 Ley N.º 30096 (Ley de Delitos Informáticos del Perú)
- **Marco de Autorización y Trazabilidad:** Base legal contra el acceso ilícito (Art. 2) y atentados contra la integridad de datos informáticos (Art. 3). La trazabilidad digital y la inmutabilidad de bitácoras garantizan la imputabilidad y el no repudio de transacciones dentro del sistema.

---

## 4. MODELO DE DATOS Y TABLAS DE AUDITORÍA

Las tablas creadas mediante la migración [`migrations/propuesta_centro_auditoria_seguridad.sql`](file:///e:/API/migrations/propuesta_centro_auditoria_seguridad.sql) estructuran el repositorio de auditoría:

```
+---------------------------------------------------------------------------------------------------+
|                                  ESQUEMA RELACIONAL DE AUDITORÍA                                  |
+---------------------------------------------------------------------------------------------------+

   +------------------------+          +------------------------+          +------------------------+
   |   dbo.AuditEvidence    |          |    dbo.AuditRisks      |          |   dbo.AuditFindings    |
   +------------------------+          +------------------------+          +------------------------+
   | EvidenceID   (PK)      |          | RiskID       (PK)      |          | FindingID    (PK)      |
   | Codigo       (PT-xx)   |          | Codigo       (RSK-xx)  |          | Codigo       (Hxx)     |
   | Entorno      (Prod/Lab)|          | Riesgo                 |          | Titulo                 |
   | Tipo                   |          | Probabilidad (1 a 5)   |          | Condicion              |
   | Descripcion            |          | Impacto      (1 a 5)   |          | Criterio               |
   | Fuente                 |          | Nivel (Bajo..Crítico)  |          | Causa                  |
   | NombreArchivo          |          | Razon                  |          | Efecto                 |
   | Sha256Hash   (64 hex)  |          | ControlExistente       |          | Recomendacion          |
   | TamanoBytes            |          | EvidenciaDisponible    |          | RiesgoAsociado (FK-txt)|
   | Custodio               |          | EvidenciaPendiente     |          | EvidenciasRel (FK-txt) |
   | FechaRegistro          |          | Estado                 |          | ProcedimientoRel       |
   +------------------------+          | CreadoEn               |          | Severidad              |
                                       | ActualizadoEn          |          | Estado (Borrador..)    |
                                       +------------------------+          | ResponsableRevision    |
                                                                           | ConfirmadoEn           |
                                                                           | CreadoEn               |
                                                                           +------------------------+

   +--------------------------+        +--------------------------+        +--------------------------+
   |  dbo.AuditRecoveryTests  |        |     dbo.AuditDmlLog      |        |     dbo.AuditDdlLog      |
   +--------------------------+        +--------------------------+        +--------------------------+
   | RecoveryID   (PK)        |        | DmlID        (PK)        |        | DdlID        (PK)        |
   | Fecha                    |        | FechaHora                |        | FechaHora                |
   | BaseRestaurada           |        | Operacion (INS/UPD/DEL)  |        | Evento (CREATE/ALTER/DRP)|
   | Ambiente                 |        | Tabla                    |        | Esquema                  |
   | Responsable              |        | Identificador            |        | Objeto                   |
   | EvidenciaAdjunta         |        | UsuarioTecnico           |        | TipoObjeto               |
   | Sha256Hash   (64 hex)    |        | ValoresAntes             |        | UsuarioSQL               |
   | Observaciones            |        | ValoresDespues           |        | Comando                  |
   | Estado (Conforme..)      |        | EsLaboratorio (BIT=1)    |        | EsLaboratorio (BIT=1)    |
   | CreadoEn                 |        | CreadoEn                 |        | CreadoEn                 |
   +--------------------------+        +--------------------------+        +--------------------------+
```

---

## 5. ESTRUCTURA DE NAVEGACIÓN (LAS 9 PESTAÑAS)

La interfaz en [`templates/admin_auditoria.html`](file:///e:/API/templates/admin_auditoria.html) está organizada en nueve pestañas funcionales:

| N.º | Pestaña | Propósito Operativo | Entorno | Fuente de Datos |
|---|---|---|---|---|
| **1** | **Resumen Ejecutivo** | Visión integral de KPIs, tarjetas corregidas y tabla de completitud/límites metodológicos de cada fuente. | Producción | Indicadores consolidados |
| **2** | **Eventos Aplicación** | Visor de `AdminAuditLog` con filtros avanzados (fecha, usuario, nivel, IP), clasificación semántica y sanitización de datos. | Producción | `dbo.AdminAuditLog` |
| **3** | **Seguridad SQL Server** | Censo de solo lectura de principales de base de datos, membresías de roles y permisos explícitos. | Producción | `sys.database_*` |
| **4** | **Trazabilidad DML/DDL** | Bitácoras de cambios en filas y esquemas obtenidos en escenario de laboratorio, con cálculo de cobertura (100% caso controlado) y advertencia visible. | Laboratorio | `AuditDmlLog`, `AuditDdlLog` |
| **5** | **Evidencias & Custodia** | Inventario de evidencias con códigos PT-xx, detalle técnico, custodio y hash SHA-256 inmutable. Incluye modal de cálculo/verificación interactiva de hashes. | Ambos | `dbo.AuditEvidence` |
| **6** | **Riesgos Preliminares** | Matriz de evaluación $P \times I$ ($1$ a $25$), diferenciación explícita de hipótesis vs hallazgo y acción para convertir riesgo en borrador. | Ambos | `dbo.AuditRisks` |
| **7** | **Hallazgos Formales** | Registro de no conformidades bajo estructura clásica de 5 atributos (Condición, Criterio, Causa, Efecto, Recomendación), con confirmación humana obligatoria. | Ambos | `dbo.AuditFindings` |
| **8** | **Backups & Recuperación** | Historial de respaldos de aplicación e inventario de pruebas de restauración verificadas con integridad en ambiente de laboratorio. | Ambos | `AdminAuditLog`, `AuditRecoveryTests` |
| **9** | **Criterios CIS / COBIT** | Matriz de cumplimiento normativo (Control → Criterio → Procedimiento → Evidencia → Resultado), con estados formales (Conforme, No Conforme, Requiere Evidencia, No Evaluable). | Ambos | Matriz estructurada |

---

## 6. CATÁLOGO DE RUTAS Y ENDPOINTS HTTP

Todas las rutas están centralizadas en [`routes/admin_auditoria.py`](file:///e:/API/routes/admin_auditoria.py) bajo el prefijo `/admin/auditoria`:

### 6.1 Vistas y Consulta
- `GET /admin/auditoria/?tab=resumen` (o cualquiera de las 9 pestañas). Requiere sesión activa con rol `SuperAdmin`. Redirige a cualquier otro usuario.

### 6.2 Exportaciones de Evidencias (CSV con BOM UTF-8 y Markdown)
Todos los archivos CSV se exportan con la cabecera `\xef\xbb\xbf` (BOM UTF-8) para garantizar su correcta apertura directa en Microsoft Excel sin problemas de codificación de caracteres en español:
1. `GET /admin/auditoria/exportar/eventos`: Exporta eventos filtrados de `AdminAuditLog`.
2. `GET /admin/auditoria/exportar/principales`: Exporta evidencia **PT-01** (`sys.database_principals`).
3. `GET /admin/auditoria/exportar/roles`: Exporta evidencia **PT-02** (`sys.database_role_members`).
4. `GET /admin/auditoria/exportar/permisos`: Exporta evidencia **PT-03** (`sys.database_permissions`).
5. `GET /admin/auditoria/exportar/trazabilidad-dml`: Exporta evidencia **PT-04** (`AuditDmlLog`).
6. `GET /admin/auditoria/exportar/trazabilidad-ddl`: Exporta evidencia **PT-05** (`AuditDdlLog`).
7. `GET /admin/auditoria/exportar/evidencias`: Exporta el catálogo completo de evidencias y hashes SHA-256.
8. `GET /admin/auditoria/exportar/riesgos`: Exporta la matriz de riesgos preliminares con severidad $P \times I$.
9. `GET /admin/auditoria/exportar/hallazgos`: Exporta los hallazgos formales documentados.
10. `GET /admin/auditoria/exportar/matriz`: Exporta la matriz de criterios CIS/COBIT y estados de evaluación.
11. `GET /admin/auditoria/exportar/informe-consolidado`: Genera y descarga el **Dossier Consolidado de Auditoría** en formato Markdown (`.md`) para integración inmediata en informes formales.

### 6.3 Operaciones de Mutación (POST con CSRF y SuperAdmin)
- `POST /admin/auditoria/evidencias/guardar`: Registra una nueva evidencia física o digital, calculando automáticamente su hash SHA-256 si se adjunta archivo.
- `POST /admin/auditoria/evidencias/verificar-hash`: Endpoint AJAX para calcular el hash SHA-256 de una carga útil y compararlo con un hash esperado.
- `POST /admin/auditoria/riesgos/guardar`: Registra o actualiza un riesgo técnico preliminar.
- `POST /admin/auditoria/riesgos/convertir-hallazgo`: Convierte un riesgo preliminar en un **Borrador** de hallazgo. **Nunca lo confirma automáticamente.**
- `POST /admin/auditoria/hallazgos/guardar`: Registra o edita los campos formales de un hallazgo de auditoría.
- `POST /admin/auditoria/hallazgos/cambiar-estado`: Actualiza el ciclo de vida del hallazgo (`Borrador` → `Requiere evidencia` → `Confirmado` → `Cerrado` / `Descartado`).
- `POST /admin/auditoria/recuperacion/guardar`: Registra una prueba de restauración realizada en laboratorio.

---

## 7. METODOLOGÍA DE EVALUACIÓN Y CADENA DE CUSTODIA

### 7.1 Cadena de Razonamiento de Auditoría
El sistema implementa el rigor metodológico exigido en auditorías de sistemas de información:

$$\text{HECHO OBSERVADO} \longrightarrow \text{EVIDENCIA EMPÍRICA} \longrightarrow \text{CRITERIO NORMATIVO} \longrightarrow \text{EVALUACIÓN DEL AUDITOR} \longrightarrow \text{HALLAZGO FORMAL}$$

- **Hecho:** Condición tangible observada en la configuración o registros (ej. cuenta de aplicación con rol `db_owner`).
- **Evidencia:** Archivo exportado y verificado con hash SHA-256 (ej. `PT-02_roles_db.csv`).
- **Criterio:** Estándar o ley aplicable (ej. CIS Benchmark §2.2 / Principio de Menor Privilegio).
- **Evaluación:** Juicio analítico del auditor humano evaluando contexto, controles compensatorios y viabilidad.
- **Hallazgo:** Declaración formal emitida solo cuando se comprueba el desvío normativo sin control compensatorio suficiente.

### 7.2 Cadena de Custodia Criptográfica (SHA-256)
- **Concepto clave:** La función hash criptográfica SHA-256 **no es un método de cifrado ni de ocultación**. Es una función unidireccional que calcula una huella digital determinista de 64 caracteres hexadecimales (256 bits).
- **Propósito forense:** SHA-256 es una huella digital que permite verificar la integridad de la evidencia y detectar modificaciones posteriores a su recolección. No cifra el contenido ni impide su modificación. Si un solo byte del archivo de evidencia es alterado con posterioridad a la extracción, su hash diferirá drásticamente del registrado originalmente en la base de datos, delatando la manipulación.

### 7.3 Matriz de Riesgos Preliminares (Probabilidad $\times$ Impacto)
El cálculo de severidad se realiza mediante una matriz estándar $5 \times 5$:
- **Probabilidad ($P$):** 1 (Muy baja) a 5 (Muy alta).
- **Impacto ($I$):** 1 (Muy bajo) a 5 (Muy alto).
- **Puntaje de Riesgo:** $\text{Score} = P \times I$ ($1$ a $25$).

| Rango de Puntaje | Nivel de Severidad | Criterio de Tratamiento |
|---|---|---|
| **$15$ a $25$** | **Crítico** | Requiere atención inmediata y control compensatorio urgente. |
| **$10$ a $14$** | **Alto** | Debe documentarse como borrador de hallazgo para evaluación formal prioritaria. |
| **$5$ a $9$** | **Medio** | Riesgo operacional moderado; planificar mitigación a mediano plazo. |
| **$1$ a $4$** | **Bajo** | Riesgo tolerable; monitorear periódicamente. |

#### Codificación Definitiva de Riesgos Preliminares (RSK):
| Código | Riesgo Preliminar | Nivel | Control Existente | Evidencia Vinculada | Estado |
|---|---|:---:|---|---|:---:|
| **RSK-01** | Dependencia de respaldo manual sin calendarización automatizada institucional | **Alto** ($P=4, I=4$) | Exportación manual de backups ZIP desde el panel web. | `AdminAuditLog` (BACKUP/GENERAR), `PT-06` | Identificado |
| **RSK-02** | Principal con membresía `db_owner` requiere identificación de propietario y justificación | **Alto** ($P=3, I=4$) | Controles de capa de aplicación Flask y sesiones RBAC. | `PT-02_roles_db.csv` (`portal_4997910989b62d8cf6d5`) | Requiere evidencia adicional |
| **RSK-03** | Ausencia de auditoría de consultas SELECT a nivel del motor de base de datos | **Medio** ($P=3, I=3$) | Logging de transacciones POST/PUT/DELETE en `AdminAuditLog`. | Restricción SmarterASP.NET / Catálogo | Identificado |
| **RSK-04** | Falta de captura continua DDL en entorno de producción compartido | **Medio** ($P=2, I=4$) | Control de versiones Git y despliegue por migraciones. | `PT-05_trazabilidad_ddl_lab.csv` | Identificado |

### 7.4 Principio de Confirmación Humana Obligatoria
Queda terminantemente prohibido que una alerta, evento o riesgo registrado por el sistema sea promovido automáticamente al estado de `Confirmado`. 
- Todo riesgo convertido pasa obligatoriamente al estado **`Borrador`**.
- La promoción a `Confirmado` exige la acción deliberada y autenticada de un auditor humano (`SuperAdmin`), quien valida que la condición observada no cuenta con controles compensatorios y que la evidencia respalda formalmente la no conformidad.

---

## 8. CATÁLOGO DE EVIDENCIAS DIGITALES DE PRUEBA (PT)

| Código | Entorno | Tipo | Archivo / Fuente | Procedimiento Relacionado | Filas | Hash SHA-256 (Verificado en Disco) | Estado |
|---|---|---|---|---|---:|---|---|
| **PT-01** | Producción | Configuración SQL | `PT-01_principales_sql.csv`<br>(`sys.database_principals`) | Censo de cuentas y logins activos en catálogo de base de datos. | 5 | `c685ef7b13a090d4dca7a6f6cd2252ab4b0a33dbb048a052eec9c4b501b76011` | **Válido** |
| **PT-02** | Producción | Membresías de Roles | `PT-02_roles_db.csv`<br>(`sys.database_role_members`) | Auditoría de asignación del rol `db_owner` (`dbo`, `portal_...`). | 2 | `e7544777070bdd68bb502452b4b7146c42d83fa78e654085bccf21367c16cffa` | **Válido** |
| **PT-03** | Producción | Permisos Explícitos | `PT-03_permisos_explicitos.csv`<br>(`sys.database_permissions`) | Examen de permisos directos `GRANT`/`DENY` a nivel objeto. | 228 | `d1c37989421f7339044053e78a6005ecdb8893a6259c8f5081fc2b66dd39b7ea` | **Válido** |
| **PT-04** | Laboratorio | Trazabilidad DML | `PT-04_trazabilidad_dml_lab.csv`<br>(`AuditDmlLog`) | Validación de captura de transacciones sobre `dbo.Salas` (2 INS, 1 UPD, 1 DEL). | 4 | `d6188f18febedf444ee672f6aad65c25f6d46b24d99fabfcb1a5c6d33b8727f2` | **Verificado** |
| **PT-05** | Laboratorio | Trazabilidad DDL | `PT-05_trazabilidad_ddl_lab.csv`<br>(`AuditDdlLog`) | Comprobación de triggers de servidor ante CREATE/ALTER/DROP sobre `dbo.AuditoriaDDL_Prueba`. | 3 | `78425d8b2fec6acd0f9155bf1d435431e36f09cee62213283d9d88b932327fca` | **Verificado** |
| **PT-06** | Laboratorio | Continuidad / Backup | `PT-06_restauracion_verificada.log`<br>(`AuditRecoveryTests`) | Ensayo de restauración con `RESTORE VERIFYONLY` y `CHECKDB` (documentado en BD; log físico pendiente). | 0 | `PENDIENTE_GENERACION_LOG_FISICO` | **Pendiente** |

---

## 9. PROCEDIMIENTOS PASO A PASO PARA EL AUDITOR

### Procedimiento 1: Censo de Seguridad y Roles en Producción
1. Inicie sesión en el sistema administrativo con una cuenta con rol `SuperAdmin`.
2. Diríjase a `/admin/auditoria/?tab=sql` (Pestaña 3: Seguridad SQL Server).
3. Inspeccione visualmente la tabla **1. Principales de Base de Datos**. Confirme que no existan cuentas huérfanas, usuarios de prueba desconocidos o permisos indebidos para el usuario `guest`.
4. Haga clic en **Exportar CSV (PT-01)** para descargar el archivo de evidencia con su respectiva huella SHA-256 de integridad para la cadena de custodia.
5. Pase a la sección **2. Roles y Membresías de Base de Datos**. Verifique las cuentas asociadas al rol `db_owner`.
6. Si detecta que la cuenta de conexión de la aplicación tiene `db_owner`, evalúe si el proveedor de hosting compartido lo exige o si es posible aplicar el principio de menor privilegio mediante roles a medida (`db_datareader`, `db_datawriter`, `db_executor`).
7. Haga clic en **Exportar CSV (PT-02)**.

### Procedimiento 2: Registro y Custodia Forense de una Nueva Evidencia
1. Diríjase a `/admin/auditoria/?tab=evidencias` (Pestaña 5: Evidencias & Custodia).
2. Haga clic en el botón superior **Registrar Evidencia**.
3. En el modal emergente:
   - Ingrese el código correlativo de evidencia (ej. `PT-07`).
   - Seleccione el entorno de origen (`Producción` o `Laboratorio`).
   - Indique el procedimiento técnico asociado (ej. `Verificación de logs de acceso web`).
   - Describa la metodología utilizada para la extracción.
   - Adjunte el archivo original (`.csv`, `.log`, `.txt`). El sistema calculará automáticamente su hash criptográfico SHA-256.
4. Haga clic en **Guardar Evidencia**.
5. Para verificar posteriormente que el archivo conservado en el repositorio no ha sido alterado, haga clic en **Verificar Hash**, pegue el contenido o archivo y compare contra el hash registrado.

### Procedimiento 3: Formalización de un Hallazgo de Auditoría
1. Diríjase a `/admin/auditoria/?tab=riesgos` (Pestaña 6: Riesgos Preliminares).
2. Localice la hipótesis de riesgo a evaluar (ej. `RSK-01: Dependencia de respaldo manual sin calendarización automatizada institucional`).
3. Haga clic en el botón **Convertir a Borrador de Hallazgo**. El sistema creará automáticamente un registro correlativo (ej. `H02`) en estado **`Borrador`**.
4. Diríjase a la Pestaña 7: **Hallazgos Formales**.
5. Localice el borrador generado y haga clic en **Editar / Detallar**.
6. Complete los 5 componentes obligatorios de la no conformidad:
   - **Condición:** Hecho exacto observado en la infraestructura.
   - **Criterio:** Estándar CIS, COBIT o Ley infringida.
   - **Causa:** Motivo técnico o de gestión que originó la condición.
   - **Efecto:** Impacto potencial en confidencialidad, integridad o disponibilidad.
   - **Recomendación:** Acción correctiva viable y sustentable.
7. Una vez contrastada toda la evidencia física y digital, haga clic en **Cambiar Estado** y seleccione **`Confirmado`**.

---

## 10. PRUEBAS AUTOMATIZADAS Y SUITE DE REGRESIÓN

El módulo cuenta con una suite completa de pruebas unitarias y de integración en [`tests/test_centro_auditoria.py`](file:///e:/API/tests/test_centro_auditoria.py):

```bash
python -m unittest tests/test_centro_auditoria.py
```

### Resultados de la Suite Automatizada:
- **Total de pruebas ejecutadas:** 22 pruebas automatizadas.
- **Resultado:** `OK` (0 fallos, 0 errores, tiempo: 37 s).
- **Cobertura de verificación:**
  1. `TestCentroAuditoriaControlAcceso`: Protección estricta contra accesos no autorizados y rechazo de solicitudes sin rol `SuperAdmin`.
  2. `TestCentroAuditoriaVistasYTabs`: Renderizado correcto y sin errores 500 de las 9 pestañas del módulo.
  3. `TestCentroAuditoriaExportaciones`: Generación de archivos CSV con BOM UTF-8 e informe en Markdown.
  4. `TestCentroAuditoriaLogicaNegocio`:
     - Cálculo exacto de SHA-256 para textos y binarios.
     - Sanitización obligatoria de datos sensibles (ocultamiento de contraseñas, tokens y hashes).
     - Clasificación semántica de acciones y severidad (`Crítico`, `Advertencia`, `Informativo`).
     - Cálculo de la escala $P \times I$ en la matriz de riesgos.
     - Conversión estricta de riesgo a borrador de hallazgo con confirmación humana obligatoria.
     - Cobertura de trazabilidad DML de laboratorio con inclusión de advertencia metodológica.
     - Manejo defensivo y reporte de restricciones de hosting compartido sin generar caídas en el sistema.

### Optimización y Tiempos de Respuesta (Antes vs Después):
Se eliminó la carga masiva y concurrente de las 9 pestañas en cada petición mediante tres mecanismos:
1. **Carga perezosa (lazy-loading):** Cada petición `tab=<nombre>` ejecuta exclusivamente las consultas correspondientes a su pestaña.
2. **Caché en memoria con TTL de 60 segundos:** Las tarjetas KPI superiores se sirven desde caché o mediante una única consulta consolidada con subconsultas `SELECT COUNT(*)`, reduciendo de 20 a 1-2 llamadas ODBC a través de WAN.
3. **Paginación en motor SQL Server y eliminación de FORMAT():** Paginación por `OFFSET...FETCH NEXT` en eventos y permisos, sustituyendo `FORMAT()` de SQL Server por `CONVERT(varchar(19), ..., 120)` para erradicar el overhead del runtime CLR.

| Pestaña | Tiempo Antes | Consultas Antes | Tiempo Después | Consultas Después | Mejora / Reducción |
|---|---:|---:|---:|---:|---:|
| **1. Resumen** | 13.68 s | 20 | 3.14 s (fría) | 2 | **-77.1%** |
| **2. Eventos Aplicación** | 11.70 s | 20 | 1.52 s | 2 | **-87.0%** |
| **3. Seguridad SQL** | 11.65 s | 20 | 2.16 s | 4 | **-81.5%** |
| **4. Trazabilidad DML/DDL** | 11.77 s | 20 | 1.19 s | 2 | **-89.9%** |
| **5. Evidencias & Custodia** | 11.77 s | 20 | 0.61 s | 1 | **-94.8%** |
| **6. Riesgos Preliminares** | 11.98 s | 20 | 0.70 s | 1 | **-94.1%** |
| **7. Hallazgos Formales** | 12.17 s | 20 | 0.58 s | 1 | **-95.2%** |
| **8. Backups & Recuperación** | 11.87 s | 20 | 0.75 s | 2 | **-93.7%** |
| **9. Criterios CIS/COBIT** | 12.21 s | 20 | 0.001 s | 0 | **>-99.9%** |

---

## 11. CONCLUSIÓN Y RECOMENDACIONES INSTITUCIONALES PARA LA UNDAC

1. **Gestión de Cuentas de Acceso (CIS §2.2):** Se recomienda formalizar con el área técnica de TI y el proveedor de hosting la sustitución progresiva de la membresía directa `db_owner` por un rol personalizado con privilegios mínimos requeridos para la ejecución de la aplicación web.
2. **Automatización de Respaldos (COBIT DSS05.04):** Dado que la generación actual de respaldos lógicos depende de descargas manuales administrativas, se aconseja programar un servicio desatendido periódico con verificación de integridad de páginas en disco.
3. **Mantenimiento de la Cadena de Custodia (Ley 29733 / Ley 30096):** Continuar registrando cada exportación de datos con su respectivo identificador y hash SHA-256 en la tabla `dbo.AuditEvidence`, resguardando la trazabilidad digital y el marco de responsabilidad institucional.
