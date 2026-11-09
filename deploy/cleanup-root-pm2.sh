#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Use sudo bash deploy/cleanup-root-pm2.sh"
  exit 1
fi

echo "==> Verificando se existe daemon PM2 do root"
if ! ps -eo user=,args= | grep -E '^root[[:space:]]+PM2 v[^ ]+: God Daemon ' >/dev/null; then
  echo "Nenhum daemon PM2 do root encontrado. Nada a remover."
  exit 0
fi

# Executar pm2 como root aqui é intencional: há um daemon root comprovado.
# Se houver aplicações, abortamos antes de matar o daemon.
echo "==> Conferindo aplicações do root"
DATA="$(env PM2_HOME=/root/.pm2 HOME=/root pm2 jlist)"
printf '%s' "$DATA" | python3 -c 'import sys,json; v=json.load(sys.stdin); print("Processos PM2 do root:",len(v)); sys.exit(0 if len(v)==0 else 2)' || {
  echo "ABORTADO: daemon root tem processos cadastrados ou não foi possível validar lista."
  exit 2
}
echo "==> Desabilitando reinício automático do daemon root, se configurado"
if systemctl list-unit-files pm2-root.service --no-legend 2>/dev/null | grep -q '^pm2-root.service'; then
  systemctl disable --now pm2-root.service
fi
echo "==> Encerrando somente o PM2 do root (lista vazia)"
env PM2_HOME=/root/.pm2 HOME=/root pm2 kill
echo "==> Verificando PM2 do linuxmanager"
runuser -u linuxmanager -- env HOME=/home/linuxmanager PM2_HOME=/home/linuxmanager/.pm2 pm2 list
echo "Limpeza concluída."
