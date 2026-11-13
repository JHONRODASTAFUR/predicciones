# Instala Predicciones Fútbol:
#   1) acceso directo en el escritorio y en el menú Inicio
#   2) tarea de Windows "PrediccionesFutbol" que actualiza los datos SOLA:
#        - cada hora
#        - al iniciar sesión en la laptop
#        - cada vez que Windows se conecta a una red (WiFi o cable)
#      y solo si hay conexión a internet.
# No necesita permisos de administrador ni a Claude.

$ErrorActionPreference = "Stop"
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction Stop).Source
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }

Write-Host "Programa en: $base"
& $py (Join-Path $base "crear_icono.py")

# ---- accesos directos
$ws = New-Object -ComObject WScript.Shell
$destinos = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
foreach ($dir in $destinos) {
    $lnk = $ws.CreateShortcut((Join-Path $dir "Predicciones Fútbol.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $base "app.pyw") + '"'
    $lnk.WorkingDirectory = $base
    $lnk.IconLocation = (Join-Path $base "icono.ico") + ",0"
    $lnk.Description = "Predicciones de partidos de fútbol (se actualiza solo)"
    $lnk.Save()
    Write-Host "Acceso directo: $dir"
}

# ---- tarea programada
$accion = New-ScheduledTaskAction -Execute $pyw -Argument ('"' + (Join-Path $base "actualizar.py") + '"') -WorkingDirectory $base

$cadaHora = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Hours 1)

$alIniciar = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$alIniciar.Delay = "PT2M"

$claseEvento = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$alConectar = New-CimInstance -CimClass $claseEvento -ClientOnly
$alConectar.Enabled = $true
$alConectar.Delay = "PT1M"
$alConectar.Subscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[(EventID=10000)]]</Select></Query></QueryList>'

$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "PrediccionesFutbol" -Action $accion -Trigger @($cadaHora, $alIniciar, $alConectar) `
    -Settings $ajustes -Principal $principal -Force `
    -Description "Actualiza solo las predicciones de fútbol (ESPN + Open-Meteo) cuando hay internet." | Out-Null

# ---- revisión rápida de alineaciones: cada 15 min mira solo los partidos de las próximas 2,5 horas
$accionRapida = New-ScheduledTaskAction -Execute $pyw -Argument ('"' + (Join-Path $base "actualizar.py") + '" --rapido') -WorkingDirectory $base
$cada15 = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Minutes 15)
$ajustesRapidos = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "PrediccionesFutbolAlineaciones" -Action $accionRapida -Trigger $cada15 `
    -Settings $ajustesRapidos -Principal $principal -Force `
    -Description "Cada 15 min: si salieron las alineaciones de un partido cercano, recalcula ese partido." | Out-Null

$t = Get-ScheduledTask -TaskName "PrediccionesFutbol"
Write-Host "Tarea programada creada: $($t.TaskName) ($($t.Triggers.Count) disparadores, estado $($t.State))"
Write-Host "Listo. Abre 'Predicciones Fútbol' desde el escritorio."
