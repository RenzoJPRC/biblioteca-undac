$ErrorActionPreference = "Continue"

$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlFile = Join-Path $projectPath "URL_PUBLICA.txt"

$publicUrl = $null
$urlGuardada = $false
$navegadorAbierto = $false

Write-Host ""
Write-Host "============================================"
Write-Host "   CLOUDFLARE TUNNEL - BIBLIOTECA UNDAC"
Write-Host "============================================"
Write-Host ""

if (Test-Path $urlFile) {
    Remove-Item $urlFile -Force
}

cloudflared tunnel --url http://localhost:5000 2>&1 | ForEach-Object {

    $line = $_.ToString()
    Write-Host $line

    # Detectar URL publica
    if ($line -match 'https://[a-zA-Z0-9-]+\.trycloudflare\.com') {

        $publicUrl = $matches[0]

        if (-not $urlGuardada) {

            Set-Content -Path $urlFile -Value $publicUrl

            try {
                Set-Clipboard -Value $publicUrl
            }
            catch {}

            Write-Host ""
            Write-Host "============================================"
            Write-Host " URL PUBLICA GENERADA"
            Write-Host "============================================"
            Write-Host ""
            Write-Host $publicUrl
            Write-Host ""
            Write-Host "Guardada en URL_PUBLICA.txt"
            Write-Host "Copiada al portapapeles"
            Write-Host ""

            $urlGuardada = $true
        }
    }

    # Cuando Cloudflare confirme la conexion
    if (
        $publicUrl -and
        (-not $navegadorAbierto) -and
        $line -match 'Registered tunnel connection'
    ) {

        Write-Host ""
        Write-Host "Tunnel conectado."
        Write-Host "Esperando que la URL publica este disponible..."
        Write-Host ""

        $disponible = $false

        for ($i = 1; $i -le 30; $i++) {

            Write-Host "Comprobando URL... intento $i/30"

            try {

                $response = Invoke-WebRequest `
                    -Uri $publicUrl `
                    -UseBasicParsing `
                    -TimeoutSec 5

                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    $disponible = $true
                    break
                }

            }
            catch {
                Start-Sleep -Seconds 2
            }
        }

        if ($disponible) {

            Write-Host ""
            Write-Host "============================================"
            Write-Host " SISTEMA ONLINE"
            Write-Host "============================================"
            Write-Host ""
            Write-Host $publicUrl
            Write-Host ""
            Write-Host "La pagina ya responde correctamente."
            Write-Host "Abriendo navegador..."
            Write-Host ""

            Start-Process $publicUrl

        }
        else {

            Write-Host ""
            Write-Host "La URL fue creada pero aun no responde."
            Write-Host "Puedes abrirla manualmente mas tarde:"
            Write-Host $publicUrl
        }

        $navegadorAbierto = $true
    }
}