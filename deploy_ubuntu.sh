#!/usr/bin/env bash
set -euo pipefail

APP_NAME="portfolio-scanner"
DEFAULT_PORT="8000"
ZIP_PATH=""
PORT="$DEFAULT_PORT"
INSTALL_DIR="/opt/${APP_NAME}"
SERVICE_NAME="${APP_NAME}.service"

usage() {
  echo "Usage: $0 -z /path/to/portfolio-scanner-v9.zip [-p 8000]"
  echo "  -z   Path to the project zip you uploaded to the server"
  echo "  -p   Port to expose the web UI on (default: ${DEFAULT_PORT})"
  exit 1
}

while getopts ":z:p:h" opt; do
  case ${opt} in
    z) ZIP_PATH="${OPTARG}" ;;
    p) PORT="${OPTARG}" ;;
    h) usage ;;
    \?) echo "Invalid option: -$OPTARG" ; usage ;;
    :) echo "Option -$OPTARG requires an argument." ; usage ;;
  esac
done

if [[ -z "${ZIP_PATH}" ]]; then
  echo "ERROR: You must provide -z /path/to/zip"
  usage
fi

if [[ $EUID -ne 0 ]]; then
   echo "Please run as root (use: sudo $0 ...)" 
   exit 1
fi

echo ">>> Installing prerequisites..."
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg lsb-release unzip ufw

if ! command -v docker >/dev/null 2>&1; then
  echo ">>> Installing Docker Engine + Compose plugin (official repo)..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

echo ">>> Preparing install dir: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
unzip -o "${ZIP_PATH}" -d "${INSTALL_DIR}"

# Optional: tweak port in docker-compose.yml if user selected a custom one.
DC="${INSTALL_DIR}/docker-compose.yml"
if [[ -f "${DC}" ]]; then
  # replace "8000:8000" with "${PORT}:8000"
  sed -i "s/8000:8000/${PORT}:8000/g" "${DC}"
fi

# Ensure .env present
if [[ -f "${INSTALL_DIR}/.env" ]]; then
  echo ">>> .env already exists — keeping it."
elif [[ -f "${INSTALL_DIR}/.env.example" ]]; then
  echo ">>> Creating .env from .env.example"
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
else
  echo ">>> No .env found; creating a minimal one."
  cat > "${INSTALL_DIR}/.env" <<EOF
BASE_CCY=AUD
DATABASE_URL=sqlite:///./scanner.db
SCAN_EVERY_MINS=10
CACHE_TTL_MIN=1440
EOF
fi

# Create a systemd unit to manage 'docker compose up -d' on boot
echo ">>> Creating systemd service ${SERVICE_NAME}"
cat > "/etc/systemd/system/${SERVICE_NAME}" <<EOF
[Unit]
Description=${APP_NAME} (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo ">>> Starting the stack..."
systemctl start "${SERVICE_NAME}"

# Basic firewall rule (optional)
if command -v ufw >/dev/null 2>&1; then
  echo ">>> Opening port ${PORT} on UFW (if enabled)"
  ufw allow "${PORT}/tcp" || true
fi

echo ">>> Done!"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Open: http://${IP:-YOUR_SERVER_IP}:${PORT}"
echo "Manage with:"
echo "  systemctl status ${SERVICE_NAME}"
echo "  systemctl restart ${SERVICE_NAME}"
echo "Logs: docker logs -f portfolio-api"
