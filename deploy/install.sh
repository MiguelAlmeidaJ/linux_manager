#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
PORT=8765
APP_USER=linuxmanager
APP_DIR=/opt/linux-manager
PROJECT_DIR=/srv/linux-manager/projects
REPO=https://github.com/MiguelAlmeidaJ/linux_manager.git

if [[ $EUID -ne 0 ]]; then echo "Execute com sudo/root"; exit 1; fi
. /etc/os-release
if [[ "$ID" != ubuntu && "$ID" != debian ]]; then echo "Somente Ubuntu ou Debian"; exit 1; fi
ARCH="$(dpkg --print-architecture)"
if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]]; then echo "Suporta amd64/arm64"; exit 1; fi

echo "==> Pacotes básicos"
apt-get update
apt-get install -y ca-certificates curl gnupg git python3 python3-venv python3-pip openssl build-essential

echo "==> Node.js 24 e PM2"
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/linux-manager-nodesource.sh
bash /tmp/linux-manager-nodesource.sh
rm -f /tmp/linux-manager-nodesource.sh
apt-get install -y nodejs
npm install -g pm2

echo "==> Docker e Docker Compose"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $VERSION_CODENAME
Components: stable
Architectures: $ARCH
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

echo "==> Usuário e projeto"
if ! id linuxmanager >/dev/null 2>&1; then useradd -m -s /bin/bash linuxmanager; fi
mkdir -p "$APP_DIR" "$PROJECT_DIR"
chown -R linuxmanager:linuxmanager "$APP_DIR" /srv/linux-manager
if [[ ! -e "$APP_DIR/.git" ]]; then
  if [[ -n "$(ls -A "$APP_DIR")" ]]; then echo "Diretório $APP_DIR não está vazio"; exit 1; fi
  runuser -u linuxmanager -- git clone "$REPO" "$APP_DIR"
else
  runuser -u linuxmanager -- git -C "$APP_DIR" pull --ff-only
fi

echo "==> Python"
runuser -u linuxmanager -- python3 -m venv "$APP_DIR/.venv"
runuser -u linuxmanager -- "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Credenciais"
if [[ ! -f "$APP_DIR/.env" ]]; then
  PASSWORD="$(openssl rand -hex 18)"
  SECRET="$(openssl rand -hex 48)"
  cat > "$APP_DIR/.env" <<EOF
ADMIN_PASSWORD=$PASSWORD
SESSION_SECRET=$SECRET
PROJECTS_DIR=$PROJECT_DIR
COOKIE_SECURE=false
TERMINAL_ENABLED=false
EOF
  chmod 600 "$APP_DIR/.env"
  chown linuxmanager:linuxmanager "$APP_DIR/.env"
  (umask 077; printf 'Senha do Linux Manager: %s\n' "$PASSWORD" > /root/linux-manager-credentials.txt)
fi

echo "==> Permissões e PM2"
usermod -aG docker linuxmanager
runuser -u linuxmanager -- env HOME=/home/linuxmanager pm2 ping >/dev/null
pm2 startup systemd -u linuxmanager --hp /home/linuxmanager >/dev/null
systemctl enable pm2-linuxmanager || true

echo "==> systemd"
sed "s@--host 127.0.0.1 --port 8765@--host 0.0.0.0 --port $PORT@" \
  "$APP_DIR/deploy/linux-manager.service" > /etc/systemd/system/linux-manager.service
systemctl daemon-reload
systemctl enable --now linux-manager
systemctl restart linux-manager

echo "==> Firewall"
if command -v ufw >/dev/null; then
  if ufw status | grep -q '^Status: active'; then
    ufw allow "$PORT/tcp" comment "Linux Manager"
    ufw reload
  else
    echo "UFW inativo. Não será habilitado automaticamente para evitar bloquear SSH."
    echo "Para habilitar: ufw allow OpenSSH && ufw allow $PORT/tcp && ufw enable"
  fi
else
  echo "UFW não instalado. Confira o firewall da VPS e do provedor."
fi

echo "==> Verificação"
node -v
npm -v
pm2 -v
docker --version
docker compose version
systemctl --no-pager --full status linux-manager | head -n 15 || true
echo "Acesso: http://IP-DO-SERVIDOR:$PORT"
echo "Senha, quando gerada na instalação: sudo cat /root/linux-manager-credentials.txt"
echo "Logs: sudo journalctl -u linux-manager -f"
echo "AVISO: Docker group equivale a root e HTTP não protege credenciais. Não habilite o terminal no acesso público HTTP."
