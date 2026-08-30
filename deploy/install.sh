#!/usr/bin/env bash
# =============================================================================
# MEIKA - VPS setup script (tested on Ubuntu 22.04 / 24.04 LTS)
# Run as root:  sudo bash deploy/install.sh
#
# What this does:
#   1. Installs system packages (Python 3, pip, venv, git).
#   2. Clones the MEIKA repo to /opt/meika.
#   3. Creates a Python venv and installs requirements.txt.
#   4. Copies a production .env into /etc/meika/.env (root-only, 0600).
#   5. Installs + enables the systemd service (auto-start on boot).
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Siddharth-devx7/MEIKA.git}"
APP_DIR="/opt/meika"
ENV_FILE="/etc/meika/.env"
SERVICE_NAME="meika"

echo "==> Updating apt and installing system packages..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl

echo "==> Cloning MEIKA repository..."
rm -rf "$APP_DIR"
git clone "$REPO_URL" "$APP_DIR"

echo "==> Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Setting up secrets directory..."
mkdir -p /etc/meika

if [ -f "$ENV_FILE" ]; then
    echo "    Found existing $ENV_FILE - keeping it."
else
    echo "    Creating template $ENV_FILE - EDIT THIS WITH YOUR REAL KEY."
    cp "$APP_DIR/deploy/.env.production.example" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
chown root:root "$ENV_FILE"

echo "==> Installing systemd service..."
cp "$APP_DIR/deploy/meika.service" /etc/systemd/system/meika.service
systemctl daemon-reload
systemctl enable meika
systemctl restart meika

echo ""
echo "==========================================================================="
echo "  MEIKA installed.  IMPORTANT NEXT STEP:"
echo ""
echo "  Edit secrets now:   sudo nano $ENV_FILE"
echo "  (put your real Gemini API key in API_KEY=)"
echo ""
echo "  Then reload:        sudo systemctl restart meika"
echo "  Check status:       sudo systemctl status meika"
echo "  See logs:           sudo journalctl -u meika -f"
echo "==========================================================================="
