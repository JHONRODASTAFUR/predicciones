#!/usr/bin/env bash
# Instala Predicciones Fútbol en un servidor Ubuntu (Oracle Cloud Always Free) para que funcione 24/7.
# Uso (en el servidor):  DOMINIO=1-2-3-4.sslip.io USUARIO=jhon CLAVE='...' bash instalar_servidor.sh
#
# Qué hace:
#  1. Instala Python (con numpy) y Caddy (servidor web con candado HTTPS automático).
#  2. Abre los puertos 80 y 443 en el firewall interno de Ubuntu (Oracle los trae cerrados).
#  3. Deja corriendo la app (servicio "predicciones") solo dentro del servidor.
#  4. Caddy la publica en https://$DOMINIO pidiendo usuario y contraseña.
#  5. Programa la actualización cada hora y la revisión de alineaciones cada 15 minutos.
set -euo pipefail
: "${DOMINIO:?falta DOMINIO}" "${USUARIO:?falta USUARIO}" "${CLAVE:?falta CLAVE}"
APP="$HOME/PrediccionesFutbol"
YO="$(whoami)"

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID%%.*}" -lt 24 ]; then
  echo "ERROR: este servidor tiene ${PRETTY_NAME:-otro sistema}. Crea la VM con 'Canonical Ubuntu 24.04' y vuelve a intentarlo."
  exit 1
fi

echo "== 1. Memoria de reserva (swap), necesaria en la VM gratis de 1 GB"
if [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 4000000 ] && [ -z "$(swapon --show --noheadings)" ]; then
  sudo fallocate -l 3G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=3072
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== 2. Programas"
sudo timedatectl set-timezone America/Lima
# una VM recién creada suele estar instalando actualizaciones: esperar a que suelte apt
cloud-init status --wait >/dev/null 2>&1 || true
for i in $(seq 120); do sudo fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 5; done
APT='sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600'
$APT update -y
$APT install -y python3-venv caddy
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet numpy
PY="$APP/.venv/bin/python"

echo "== 2b. Firewall interno (puertos 80 y 443)"
for p in 80 443; do
  sudo iptables -C INPUT -m state --state NEW -p tcp --dport $p -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 5 -m state --state NEW -p tcp --dport $p -j ACCEPT
done
sudo netfilter-persistent save 2>/dev/null || true

echo "== 3. La app como servicio"
sudo tee /etc/systemd/system/predicciones.service >/dev/null <<EOF
[Unit]
Description=Predicciones Futbol (pagina)
After=network-online.target
[Service]
User=$YO
WorkingDirectory=$APP
ExecStart=$PY $APP/servidor.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF

echo "== 4. Actualizaciones automáticas"
sudo tee /etc/systemd/system/predicciones-actualizar.service >/dev/null <<EOF
[Unit]
Description=Predicciones Futbol: actualizacion completa
[Service]
Type=oneshot
User=$YO
WorkingDirectory=$APP
ExecStart=$PY $APP/actualizar.py
TimeoutStartSec=3h
Nice=10
OOMScoreAdjust=500
EOF
sudo tee /etc/systemd/system/predicciones-actualizar.timer >/dev/null <<EOF
[Unit]
Description=Cada hora
[Timer]
OnCalendar=*-*-* *:32:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo tee /etc/systemd/system/predicciones-alineaciones.service >/dev/null <<EOF
[Unit]
Description=Predicciones Futbol: alineaciones confirmadas
[Service]
Type=oneshot
User=$YO
WorkingDirectory=$APP
ExecStart=$PY $APP/actualizar.py --rapido
TimeoutStartSec=30min
Nice=10
OOMScoreAdjust=500
EOF
sudo tee /etc/systemd/system/predicciones-alineaciones.timer >/dev/null <<EOF
[Unit]
Description=Cada 15 minutos
[Timer]
OnCalendar=*:05/15
[Install]
WantedBy=timers.target
EOF

echo "== 5. Candado y contraseña"
HASH="$(caddy hash-password --plaintext "$CLAVE")"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMINIO {
	basicauth {
		$USUARIO $HASH
	}
	encode gzip
	reverse_proxy 127.0.0.1:8765
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now predicciones.service predicciones-actualizar.timer predicciones-alineaciones.timer
sudo systemctl restart predicciones.service     # carga el código nuevo si es una re-subida
sudo systemctl restart caddy
echo "== Listo: https://$DOMINIO"
systemctl list-timers 'predicciones-*' --no-pager
