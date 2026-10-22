SELECT AlumnoID, CAST(FechaHora AS DATE) AS Fecha, COUNT_BIG(*) AS Visitas
FROM dbo.RegistroIngresos
WHERE FechaHora >= ? AND FechaHora < ? AND FechaHora <= ? AND Sede = ?
 AND AlumnoID IS NOT NULL AND ISNULL(EsPrueba, 0) = 0
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)
GROUP BY AlumnoID, CAST(FechaHora AS DATE)
ORDER BY Fecha, AlumnoID
