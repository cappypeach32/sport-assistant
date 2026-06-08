#!/usr/bin/env bash
# Deploy AI Sports Assistant on a VPS (tested: Ubuntu/Debian).
# Usage (as root): bash deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-sports-assistant}"
REPO_URL="${REPO_URL:-https://github.com/cappypeach32/sport-assistant.git}"
BRANCH="${BRANCH:-main}"
APP_USER="${APP_USER:-www-data}"
INTERNAL_PORT="${INTERNAL_PORT:-8765}"
PUBLIC_IP="${PUBLIC_IP:-212.227.188.126}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh"
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip nginx curl

echo "==> Syncing application to ${APP_DIR}"
git config --global --add safe.directory "${APP_DIR}" 2>/dev/null || true
mkdir -p "$(dirname "$APP_DIR")"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
fi

echo "==> Python virtualenv + dependencies"
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  echo ""
  echo "!! Created ${APP_DIR}/.env from template."
  echo "!! Edit API_FOOTBALL_KEY and OPENAI_API_KEY before going live:"
  echo "   nano ${APP_DIR}/.env"
  echo ""
fi

# Ensure bind settings for reverse proxy
grep -q '^APP_HOST=' "${APP_DIR}/.env" || echo "APP_HOST=0.0.0.0" >> "${APP_DIR}/.env"
grep -q '^APP_PORT=' "${APP_DIR}/.env" || echo "APP_PORT=${INTERNAL_PORT}" >> "${APP_DIR}/.env"
grep -q '^PUBLIC_APP_URL=' "${APP_DIR}/.env" || echo "PUBLIC_APP_URL=http://${PUBLIC_IP}" >> "${APP_DIR}/.env"

chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"

echo "==> systemd service"
cp "${APP_DIR}/deploy/systemd/ai-sports-assistant.service" /etc/systemd/system/ai-sports-assistant.service
systemctl daemon-reload
systemctl enable ai-sports-assistant
systemctl restart ai-sports-assistant

echo "==> nginx"
rm -f /etc/nginx/sites-enabled/default
cp "${APP_DIR}/deploy/nginx/ai-sports-assistant.conf" /etc/nginx/sites-available/ai-sports-assistant.conf
ln -sf /etc/nginx/sites-available/ai-sports-assistant.conf /etc/nginx/sites-enabled/ai-sports-assistant.conf
nginx -t
systemctl enable nginx
systemctl restart nginx

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
fi

echo ""
echo "Deploy complete."
echo "  Overlay UI:  http://${PUBLIC_IP}/overlay"
echo "  Health:      http://${PUBLIC_IP}/"
echo "  Service:     systemctl status ai-sports-assistant"
echo ""
systemctl --no-pager --full status ai-sports-assistant | sed -n '1,12p' || true
