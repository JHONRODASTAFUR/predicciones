# Sube Predicciones Fútbol al servidor de Oracle y lo deja funcionando 24/7.
# Uso:  .\subir.ps1 -IP 123.45.67.89 -Llave C:\Users\MSI-KATANA\.ssh\ssh-key-2026-11-13.key -Correo tu@correo.com
# Se puede repetir cuando cambie el programa: vuelve a subir el código (y los datos solo si se pide -ConDatos).
# Acceso: la página pide tu correo (como usuario) y una clave aleatoria que solo queda en tu escritorio.
param(
    [Parameter(Mandatory)][string]$IP,
    [Parameter(Mandatory)][string]$Llave,
    [Parameter(Mandatory)][Alias("Usuario")][string]$Correo,
    [switch]$ConDatos
)
$Usuario = $Correo.Trim().ToLower()
if ($Usuario -notmatch '^[^@\s'']+@[^@\s'']+\.[^@\s'']+$') { throw "El correo no parece válido: $Correo" }
$ErrorActionPreference = "Stop"
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dominio = ($IP -replace '\.', '-') + ".sslip.io"
$destino = "ubuntu@$IP"
$opts = @("-i", $Llave, "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR")
# corta con un mensaje claro si un paso falla (en vez de decir "Listo" sin estarlo)
function Revisar($paso) { if ($LASTEXITCODE -ne 0) { throw "Falló: $paso (código $LASTEXITCODE). Revisa los mensajes de arriba; no se terminó la subida." } }
if (-not (Test-Path $Llave)) { throw "No encuentro la llave en: $Llave" }

# La llave de SSH solo debe poder leerla tu usuario (si no, ssh se niega a usarla)
icacls $Llave /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null; Revisar "permisos de la llave (icacls)"

# Contraseña de la página: se crea una sola vez y se guarda en tu escritorio (no se muestra en pantalla)
$archivoClave = Join-Path ([Environment]::GetFolderPath("Desktop")) "Acceso pagina predicciones.txt"
if (-not (Test-Path $archivoClave)) {
    $chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
    $bytes = New-Object byte[] 20
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $clave = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    @"
Página de Predicciones Fútbol (solo tú)
Dirección: https://$dominio
Usuario:   $Usuario  (tu correo)
Clave:     $clave

Guárdala en un lugar seguro. Si la pierdes, pídele a Claude que la cambie.
"@ | Out-File -FilePath $archivoClave -Encoding utf8
}
$clave = ((Get-Content $archivoClave -Encoding utf8) | Where-Object { $_ -like "Clave:*" }) -replace "^Clave:\s*", ""

# Empaquetar (sin registros ni archivos temporales; datos solo la primera vez o con -ConDatos)
$tmp = Join-Path $env:TEMP "predicciones_subida"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null
$incluir = @("motor", "web", "servidor", "actualizar.py", "servidor.py", "prompt_maestro.txt", "prompt_maestro.pdf", "LEEME.txt")
$ErrorActionPreference = "Continue"     # en PowerShell 5.1 cualquier aviso de ssh cortaría el script
$existe = ssh @opts $destino "test -d ~/PrediccionesFutbol/datos && echo si" 2>$null
$ErrorActionPreference = "Stop"
$primera = $ConDatos -or ($existe -ne "si")
if ($primera) { $incluir += "datos" }
foreach ($x in $incluir) { Copy-Item (Join-Path $base $x) (Join-Path $tmp $x) -Recurse }
Get-ChildItem $tmp -Recurse -Include "*.lock", "*.tmp", "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$zip = Join-Path $env:TEMP "predicciones.tar"
Remove-Item $zip -ErrorAction SilentlyContinue
tar -cf $zip -C $tmp .; Revisar "empaquetar (tar)"
Write-Host "Subiendo $([math]::Round((Get-Item $zip).Length/1MB)) MB a $IP ..."
scp @opts $zip "${destino}:/tmp/predicciones.tar"; Revisar "subir el paquete (scp)"
ssh @opts $destino "mkdir -p ~/PrediccionesFutbol && tar -xf /tmp/predicciones.tar -C ~/PrediccionesFutbol && rm /tmp/predicciones.tar"; Revisar "descomprimir en el servidor"

# Instalar / actualizar en el servidor (la clave viaja cifrada por SSH y solo se guarda como hash)
$cmd = "cd ~/PrediccionesFutbol/servidor && DOMINIO='$dominio' USUARIO='$Usuario' CLAVE='$clave' bash instalar_servidor.sh"
ssh @opts $destino $cmd; Revisar "instalar en el servidor"
Remove-Item $tmp -Recurse -Force; Remove-Item $zip
Write-Host "Listo: https://$dominio  (entra con tu correo y la clave de '$archivoClave')"
