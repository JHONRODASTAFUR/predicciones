# Quita la actualización automática y los accesos directos de Predicciones Fútbol.
# La carpeta del programa (con tus datos e historial de aciertos) se conserva: bórrala a mano si ya no la quieres.

Unregister-ScheduledTask -TaskName "PrediccionesFutbol" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "PrediccionesFutbolAlineaciones" -Confirm:$false -ErrorAction SilentlyContinue
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $lnk = Join-Path $dir "Predicciones Fútbol.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
}
Write-Host "Predicciones Fútbol desinstalado (la carpeta del programa se conserva)."
