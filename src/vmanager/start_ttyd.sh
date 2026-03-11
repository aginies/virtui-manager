#!/bin/bash
# Securely initialize and start Virtui-Manager with ttyd

# 1. Generate SSL Certificates (Self-signed) if they don't exist
CERT_DIR="/tmp/certs"
mkdir -p "$CERT_DIR"
if [[ ! -f "$CERT_DIR/ttyd.crt" || ! -f "$CERT_DIR/ttyd.key" ]]; then
    echo "Generating self-signed SSL certificate..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_DIR/ttyd.key" -out "$CERT_DIR/ttyd.crt" \
        -subj "/C=US/ST=State/L=City/O=VirtuiManager/OU=TUI/CN=localhost"
    chmod 600 "$CERT_DIR/ttyd.key"
fi

# 2. Setup Credentials
#export TMUX_TMPDIR=/tmp
WEB_USER="${WEB_USER:-admin}"
if [[ -z "$WEB_PASSWORD" ]]; then
    WEB_PASSWORD=$(openssl rand -base64 12)
    echo "***************************************************"
    echo "  GENERATED WEB CREDENTIALS:"
    echo "  Username: $WEB_USER"
    echo "  Password: $WEB_PASSWORD"
    echo "***************************************************"
else
    echo "Using provided WEB_USER and WEB_PASSWORD."
fi

# 3. Start ttyd with SSL, Auth, and tmux
# -W: Allow writing
# -c: Credentials
# -S, -C, -K: SSL
# tmux new-session -A -s virtui: Attach to or create session 'virtui'
exec ttyd \
    -W \
    -p 7681 \
    -S \
    -C "$CERT_DIR/ttyd.crt" \
    -K "$CERT_DIR/ttyd.key" \
    -c "$WEB_USER:$WEB_PASSWORD" \
    -t fontSize=14 \
    -t theme='{"background": "#1e1e1e"}' \
    --max-clients 1 \
    tmux new-session -n VirtUI-Manager -A -s virtui "python3 virtui_dev.py"
