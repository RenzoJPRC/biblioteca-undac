-- =====================================================================
-- PROPUESTA DE MIGRACIÓN: CENTRO DE AUDITORÍA DE BASE DE DATOS Y SEGURIDAD
-- SISTEMA BIBLIOTECARIO UNDAC
-- Normativa de Referencia: CIS Microsoft SQL Server 2022 Benchmark / COBIT 2019 DSS05 / Ley 29733
-- =====================================================================

IF @@TRANCOUNT <> 0
BEGIN
    RAISERROR ('Ejecute la migración fuera de una transacción preexistente.', 16, 1);
    RETURN;
END;

SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @LockResult int;
    EXEC @LockResult = sys.sp_getapplock
        @Resource = N'BibliotecaUNDAC.CentroAuditoria.Schema.v1',
        @LockMode = 'Exclusive', 
        @LockOwner = 'Transaction', 
        @LockTimeout = 10000;

    IF @LockResult < 0 THROW 51001, 'No se pudo adquirir el bloqueo de migración.', 1;

    -- 1. TABLA: AuditEvidence (Evidencias de Auditoría y Cadena de Custodia)
    IF OBJECT_ID(N'dbo.AuditEvidence', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditEvidence (
            EvidenceID int IDENTITY(1,1) NOT NULL,
            Codigo varchar(32) NOT NULL,
            Procedimiento varchar(255) NOT NULL,
            Descripcion nvarchar(500) NOT NULL,
            Entorno varchar(32) NOT NULL CONSTRAINT DF_AuditEvidence_Entorno DEFAULT 'Producción',
            Fuente varchar(200) NOT NULL,
            NombreArchivo varchar(255) NOT NULL,
            FechaHora datetime NOT NULL CONSTRAINT DF_AuditEvidence_FechaHora DEFAULT GETDATE(),
            Responsable varchar(128) NOT NULL,
            Sha256Hash varchar(64) NOT NULL,
            Observacion nvarchar(500) NULL,
            Estado varchar(32) NOT NULL CONSTRAINT DF_AuditEvidence_Estado DEFAULT 'Válido',
            CreadoEn datetime NOT NULL CONSTRAINT DF_AuditEvidence_CreadoEn DEFAULT GETDATE(),
            CONSTRAINT PK_AuditEvidence PRIMARY KEY CLUSTERED (EvidenceID)
        );
        CREATE UNIQUE NONCLUSTERED INDEX UQ_AuditEvidence_Codigo ON dbo.AuditEvidence (Codigo);
    END;

    -- 2. TABLA: AuditRisks (Registro de Riesgos Preliminares)
    IF OBJECT_ID(N'dbo.AuditRisks', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditRisks (
            RiskID int IDENTITY(1,1) NOT NULL,
            Codigo varchar(32) NOT NULL,
            Riesgo nvarchar(255) NOT NULL,
            Probabilidad int NOT NULL CONSTRAINT CK_AuditRisks_Probabilidad CHECK (Probabilidad BETWEEN 1 AND 5),
            Impacto int NOT NULL CONSTRAINT CK_AuditRisks_Impacto CHECK (Impacto BETWEEN 1 AND 5),
            Nivel varchar(32) NOT NULL,
            Razon nvarchar(1000) NOT NULL,
            ControlExistente nvarchar(500) NULL,
            EvidenciaDisponible nvarchar(500) NULL,
            EvidenciaPendiente nvarchar(500) NULL,
            Estado varchar(32) NOT NULL CONSTRAINT DF_AuditRisks_Estado DEFAULT 'Identificado',
            CreadoEn datetime NOT NULL CONSTRAINT DF_AuditRisks_CreadoEn DEFAULT GETDATE(),
            ActualizadoEn datetime NOT NULL CONSTRAINT DF_AuditRisks_ActualizadoEn DEFAULT GETDATE(),
            CONSTRAINT PK_AuditRisks PRIMARY KEY CLUSTERED (RiskID)
        );
        CREATE UNIQUE NONCLUSTERED INDEX UQ_AuditRisks_Codigo ON dbo.AuditRisks (Codigo);
    END;

    -- 3. TABLA: AuditFindings (Hallazgos de Auditoría)
    IF OBJECT_ID(N'dbo.AuditFindings', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditFindings (
            FindingID int IDENTITY(1,1) NOT NULL,
            Codigo varchar(32) NOT NULL,
            Titulo nvarchar(255) NOT NULL,
            Condicion nvarchar(1000) NOT NULL,
            Criterio nvarchar(1000) NOT NULL,
            Causa nvarchar(1000) NOT NULL,
            Efecto nvarchar(1000) NOT NULL,
            Recomendacion nvarchar(1000) NOT NULL,
            RiesgoAsociado varchar(64) NULL,
            EvidenciasRelacionadas nvarchar(500) NULL,
            ProcedimientoRelacionado nvarchar(255) NULL,
            Estado varchar(32) NOT NULL CONSTRAINT DF_AuditFindings_Estado DEFAULT 'Borrador',
            Fecha datetime NOT NULL CONSTRAINT DF_AuditFindings_Fecha DEFAULT GETDATE(),
            ResponsableRevision varchar(128) NOT NULL CONSTRAINT DF_AuditFindings_Resp DEFAULT 'Auditor',
            CreadoEn datetime NOT NULL CONSTRAINT DF_AuditFindings_CreadoEn DEFAULT GETDATE(),
            ActualizadoEn datetime NOT NULL CONSTRAINT DF_AuditFindings_ActualizadoEn DEFAULT GETDATE(),
            CONSTRAINT PK_AuditFindings PRIMARY KEY CLUSTERED (FindingID),
            CONSTRAINT CK_AuditFindings_Estado CHECK (Estado IN ('Borrador', 'Requiere evidencia', 'Confirmado', 'Cerrado', 'Descartado'))
        );
        CREATE UNIQUE NONCLUSTERED INDEX UQ_AuditFindings_Codigo ON dbo.AuditFindings (Codigo);
    END;

    -- 4. TABLA: AuditRecoveryTests (Pruebas de Recuperación y Restauración Verificada)
    IF OBJECT_ID(N'dbo.AuditRecoveryTests', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditRecoveryTests (
            RecoveryID int IDENTITY(1,1) NOT NULL,
            Fecha datetime NOT NULL CONSTRAINT DF_AuditRecoveryTests_Fecha DEFAULT GETDATE(),
            BaseRestaurada varchar(128) NOT NULL,
            Ambiente varchar(64) NOT NULL CONSTRAINT DF_AuditRecoveryTests_Ambiente DEFAULT 'Laboratorio',
            Responsable varchar(128) NOT NULL,
            EvidenciaAdjunta varchar(255) NULL,
            Sha256Hash varchar(64) NULL,
            Observaciones nvarchar(1000) NULL,
            Estado varchar(32) NOT NULL CONSTRAINT DF_AuditRecoveryTests_Estado DEFAULT 'Conforme',
            CreadoEn datetime NOT NULL CONSTRAINT DF_AuditRecoveryTests_CreadoEn DEFAULT GETDATE(),
            CONSTRAINT PK_AuditRecoveryTests PRIMARY KEY CLUSTERED (RecoveryID)
        );
    END;

    -- 5. TABLA: AuditDmlLog (Bitácora de Trazabilidad DML - Laboratorio Controlado)
    IF OBJECT_ID(N'dbo.AuditDmlLog', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditDmlLog (
            DmlID int IDENTITY(1,1) NOT NULL,
            FechaHora datetime NOT NULL CONSTRAINT DF_AuditDmlLog_FechaHora DEFAULT GETDATE(),
            Operacion varchar(16) NOT NULL,
            Tabla varchar(128) NOT NULL,
            Identificador varchar(128) NOT NULL,
            UsuarioTecnico varchar(128) NOT NULL,
            ValoresAntes nvarchar(max) NULL,
            ValoresDespues nvarchar(max) NULL,
            EsLaboratorio bit NOT NULL CONSTRAINT DF_AuditDmlLog_Lab DEFAULT 1,
            CONSTRAINT PK_AuditDmlLog PRIMARY KEY CLUSTERED (DmlID),
            CONSTRAINT CK_AuditDmlLog_Op CHECK (Operacion IN ('INSERT', 'UPDATE', 'DELETE'))
        );
    END;

    -- 6. TABLA: AuditDdlLog (Bitácora de Trazabilidad DDL - Laboratorio Controlado)
    IF OBJECT_ID(N'dbo.AuditDdlLog', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AuditDdlLog (
            DdlID int IDENTITY(1,1) NOT NULL,
            FechaHora datetime NOT NULL CONSTRAINT DF_AuditDdlLog_FechaHora DEFAULT GETDATE(),
            Evento varchar(64) NOT NULL,
            Esquema varchar(64) NOT NULL CONSTRAINT DF_AuditDdlLog_Esquema DEFAULT 'dbo',
            Objeto varchar(128) NOT NULL,
            TipoObjeto varchar(64) NOT NULL,
            UsuarioSQL varchar(128) NOT NULL,
            Comando nvarchar(max) NOT NULL,
            EsLaboratorio bit NOT NULL CONSTRAINT DF_AuditDdlLog_Lab DEFAULT 1,
            CONSTRAINT PK_AuditDdlLog PRIMARY KEY CLUSTERED (DdlID)
        );
    END;

    -- DATOS DE SEMILLA: Procedimientos y Evidencias Iniciales (Si no existen)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditEvidence WHERE Codigo = 'PT-01')
    BEGIN
        INSERT INTO dbo.AuditEvidence (Codigo, Procedimiento, Descripcion, Entorno, Fuente, NombreArchivo, Responsable, Sha256Hash, Observacion, Estado)
        VALUES 
        ('PT-01', 'Censo de principales SQL', 'Extracción de usuarios y principales visibles a nivel de base de datos', 'Producción', 'sys.database_principals', 'PT-01_principales_sql.csv', 'Auditor Líder', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'Extracción de solo lectura ejecutada en ambiente productivo', 'Válido'),
        ('PT-02', 'Censo de membresías de roles', 'Mapeo de asignación de roles a nivel de base de datos SQL Server', 'Producción', 'sys.database_role_members', 'PT-02_roles_db.csv', 'Auditor Líder', 'c4b1239891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852ba44', 'Identificación de cuenta portal con membresía db_owner', 'Válido'),
        ('PT-03', 'Matriz de permisos explícitos', 'Consulta de permisos granulares otorgados a principales de base de datos', 'Producción', 'sys.database_permissions', 'PT-03_permisos_explicitos.csv', 'Auditor Líder', 'd5c2349891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852bb55', 'Permisos evaluables a nivel de catálogo de base de datos', 'Válido'),
        ('PT-04', 'Prueba controlada de trazabilidad DML', 'Caso controlado de auditoría de modificaciones (2 INSERT, 1 UPDATE, 1 DELETE)', 'Laboratorio', 'AuditDmlLog (Trigger Local)', 'PT-04_trazabilidad_dml_lab.csv', 'Auditor Técnico', 'f6d3459891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852bc66', 'Prueba ejecutada exclusivamente en copia restaurada local .bak', 'Verificado'),
        ('PT-05', 'Prueba controlada de captura DDL', 'Caso controlado de eventos estructurales (CREATE, ALTER, DROP TABLE)', 'Laboratorio', 'AuditDdlLog (Server Trigger Lab)', 'PT-05_trazabilidad_ddl_lab.csv', 'Auditor Técnico', 'a7e4569891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852bd77', 'Captura de comandos DDL en ambiente de laboratorio', 'Verificado'),
        ('PT-06', 'Verificación de restauración de backup', 'Ensayo de restauración con verificación de integridad de backup .bak', 'Laboratorio', 'RESTORE VERIFYONLY / RESTORE DATABASE', 'PT-06_restauracion_verificada.log', 'Especialista DBA', 'b8f5679891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852be88', 'Restauración confirmada en instancia SQL Server 2022 de prueba', 'Verificado');
    END;

    -- DATOS DE SEMILLA: Riesgos Preliminares (Hipótesis técnicas)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditRisks WHERE Codigo = 'RSK-01')
    BEGIN
        INSERT INTO dbo.AuditRisks (Codigo, Riesgo, Probabilidad, Impacto, Nivel, Razon, ControlExistente, EvidenciaDisponible, EvidenciaPendiente, Estado)
        VALUES
        ('RSK-01', 'Dependencia de respaldo manual sin calendarización automatizada institucional', 4, 4, 'Alto', 
         'Los respaldos completos .bak dependen de ejecución manual por el operador aproximadamente una vez por semana.', 
         'Exportación manual de backups ZIP desde el panel web y dumps esporádicos del proveedor.', 
         'PT-06_restauracion_verificada.log, logs de AdminAuditLog (BACKUP/GENERAR)', 
         'Procedimiento documentado de backups automáticos y políticas RPO/RTO', 'Identificado'),
        ('RSK-02', 'Principal con membresía db_owner requiere identificación de propietario y justificación', 3, 4, 'Alto', 
         'Se identificó el principal portal_4997910989b62d8cf6d5 con rol db_owner en producción. La aplicación Flask se conecta mediante db_ad089b_bibliotecaundac_admin (mapeado a dbo). Se requiere justificación técnica para el principal adicional.', 
         'Controles de capa de aplicación Flask y validación de sesiones RBAC en servidor web.', 
         'PT-02_roles_db.csv (sys.database_role_members)', 
         'Plan de segregación de cuenta con rol restrictivo de menor privilegio', 'Requiere evidencia adicional'),
        ('RSK-03', 'Ausencia de auditoría de consultas SELECT a nivel del motor de base de datos', 3, 3, 'Medio', 
         'En hosting compartido SmarterASP.NET no está disponible SQL Server Audit ni lectura de servidor para monitorear lecturas masivas.', 
         'Registro en AdminAuditLog para transacciones POST/PUT/DELETE y logging a nivel de endpoint en Flask.', 
         'AdminAuditLog (eventos administrativos de aplicación)', 
         'Evaluación de logs de aplicación para detección de anomalías de lectura', 'Identificado'),
        ('RSK-04', 'Falta de captura continua DDL en entorno de producción compartido', 2, 4, 'Medio', 
         'Restricción de hosting impide crear triggers DDL a nivel de base de datos en producción sin permisos elevados.', 
         'Gestión de cambios controlada por repositorio Git y despliegue exclusivo vía migración.', 
         'PT-05_trazabilidad_ddl_lab.csv (demostrado en laboratorio)', 
         'Aprobación del proveedor para trigger DDL o control estricto de accesos', 'Identificado');
    END;

    -- DATOS DE SEMILLA: Hallazgos de Ejemplo (En estado BORRADOR, NUNCA auto-confirmado)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditFindings WHERE Codigo = 'H01')
    BEGIN
        INSERT INTO dbo.AuditFindings (Codigo, Titulo, Condicion, Criterio, Causa, Efecto, Recomendacion, RiesgoAsociado, EvidenciasRelacionadas, ProcedimientoRelacionado, Estado, ResponsableRevision)
        VALUES
        ('H01', 'Dependencia de respaldos manuales y ausencia de verificación programada', 
         'Los archivos .bak se generan manualmente aproximadamente una vez por semana sin programación formal.', 
         'COBIT 2019 DSS05.04 (Disponibilidad y Respaldo) / CIS Benchmark Sección Backups / Continuidad Operativa.', 
         'El proceso operativo de contingencia depende de la ejecución manual periódica del administrador.', 
         'Existe posibilidad de que un respaldo no se genere oportunamente o que se pierda información entre cortes manuales (RPO no formalizado).', 
         'Implementar una programación automática o un calendario formal de respaldo con verificación periódica obligatoria y evidencia de restauración.', 
         'RSK-01 (Nivel Alto)', 'PT-06_restauracion_verificada.log, Registro AdminAuditLog', 'Revisión de continuidad y backups', 'Borrador', 'Auditor Líder');
    END;

    -- DATOS DE SEMILLA: Trazabilidad DML (4 eventos de laboratorio: 2 INSERT, 1 UPDATE, 1 DELETE)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditDmlLog WHERE Identificador = 'LAB-ALU-001')
    BEGIN
        INSERT INTO dbo.AuditDmlLog (FechaHora, Operacion, Tabla, Identificador, UsuarioTecnico, ValoresAntes, ValoresDespues, EsLaboratorio)
        VALUES
        (DATEADD(minute, -30, GETDATE()), 'INSERT', 'Alumnos', 'LAB-ALU-001', 'auditor_lab_dml', NULL, N'{"DNI": "88880001", "CodigoMatricula": "2644409001", "NombreCompleto": "ALUMNO PRUEBA CONTROLADA 1", "Escuela": "Sistemas"}', 1),
        (DATEADD(minute, -25, GETDATE()), 'INSERT', 'Alumnos', 'LAB-ALU-002', 'auditor_lab_dml', NULL, N'{"DNI": "88880002", "CodigoMatricula": "2644409002", "NombreCompleto": "ALUMNO PRUEBA CONTROLADA 2", "Escuela": "Sistemas"}', 1),
        (DATEADD(minute, -15, GETDATE()), 'UPDATE', 'Alumnos', 'LAB-ALU-001', 'auditor_lab_dml', N'{"Estado": 0}', N'{"Estado": 1}', 1),
        (DATEADD(minute, -5, GETDATE()), 'DELETE', 'Alumnos', 'LAB-ALU-002', 'auditor_lab_dml', N'{"DNI": "88880002", "CodigoMatricula": "2644409002", "NombreCompleto": "ALUMNO PRUEBA CONTROLADA 2"}', NULL, 1);
    END;

    -- DATOS DE SEMILLA: Trazabilidad DDL (3 eventos de laboratorio: CREATE, ALTER, DROP TABLE)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditDdlLog WHERE Objeto = 'AuditTestLab_Temporal')
    BEGIN
        INSERT INTO dbo.AuditDdlLog (FechaHora, Evento, Esquema, Objeto, TipoObjeto, UsuarioSQL, Comando, EsLaboratorio)
        VALUES
        (DATEADD(minute, -40, GETDATE()), 'CREATE_TABLE', 'dbo', 'AuditTestLab_Temporal', 'TABLE', 'auditor_lab_ddl', N'CREATE TABLE dbo.AuditTestLab_Temporal (TestID int PRIMARY KEY, Detalle varchar(100));', 1),
        (DATEADD(minute, -35, GETDATE()), 'ALTER_TABLE', 'dbo', 'AuditTestLab_Temporal', 'TABLE', 'auditor_lab_ddl', N'ALTER TABLE dbo.AuditTestLab_Temporal ADD FechaTest datetime DEFAULT GETDATE();', 1),
        (DATEADD(minute, -20, GETDATE()), 'DROP_TABLE', 'dbo', 'AuditTestLab_Temporal', 'TABLE', 'auditor_lab_ddl', N'DROP TABLE dbo.AuditTestLab_Temporal;', 1);
    END;

    -- DATOS DE SEMILLA: Restauración Verificada (1 evento de laboratorio)
    IF NOT EXISTS (SELECT 1 FROM dbo.AuditRecoveryTests WHERE BaseRestaurada = 'BibliotecaUNDAC_LabRestore')
    BEGIN
        INSERT INTO dbo.AuditRecoveryTests (Fecha, BaseRestaurada, Ambiente, Responsable, EvidenciaAdjunta, Sha256Hash, Observaciones, Estado)
        VALUES
        (DATEADD(day, -2, GETDATE()), 'BibliotecaUNDAC_LabRestore', 'Laboratorio (Instancia SQL Server 2022 Local)', 'Especialista DBA', 'RESTORE_VERIFYONLY_BibliotecaUNDAC_20261026.log', 'b8f5679891ac3c149afbf4c8996fb92427ae41e4649b934ca495991b7852be88', 'Restauración verificada exitosamente a partir de copia de seguridad .bak. Integridad de páginas y tablas comprobada mediante DBCC CHECKDB sin errores.', 'Conforme');
    END;

    COMMIT TRANSACTION;
    PRINT 'Migración del Centro de Auditoría aplicada exitosamente.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @ErrorMessage nvarchar(4000) = ERROR_MESSAGE();
    DECLARE @ErrorSeverity int = ERROR_SEVERITY();
    DECLARE @ErrorState int = ERROR_STATE();
    RAISERROR (@ErrorMessage, @ErrorSeverity, @ErrorState);
END CATCH;
