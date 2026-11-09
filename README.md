# Linux Manager

Painel de hospedagem para **um único servidor Linux por instalação**. FastAPI, interface web, métricas, projetos Git, PM2, Docker Compose e terminal.

## Requisitos

- Ubuntu/Debian ou outra distribuição Linux com Python 3.11+
- Git instalado
- Node.js + PM2 e/ou Docker Compose opcionais
- Usuário Linux dedicado (sem root), com permissões explícitas somente nos projetos que administra
- HTTPS via proxy reverso (Nginx/Caddy), ou acesso restrito por VPN

## Instalação

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
sudo useradd -m -s /bin/bash linuxmanager || true
sudo mkdir -p /opt/linux-manager /srv/linux-manager/projects
sudo chown -R linuxmanager:linuxmanager /opt/linux-manager /srv/linux-manager
sudo -iu linuxmanager
cd /opt/linux-manager
git clone https://github.com/MiguelAlmeidaJ/linux_manager.git .
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edite a senha e a chave aleatória em .env
set -a; source .env; set +a
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8765
```

Abra o painel por túnel SSH na configuração inicial: `ssh -L 8765:127.0.0.1:8765 usuario@servidor` e visite `http://localhost:8765`.

Para acesso remoto contínuo, use Nginx/Caddy com HTTPS, encaminhando os WebSockets do terminal, e configure `COOKIE_SECURE=true`. **Não exponha a porta do Uvicorn diretamente na internet.**

## Variáveis de ambiente

| Variável | Uso |
|---|---|
| `ADMIN_PASSWORD` | Senha de acesso (obrigatória) |
| `SESSION_SECRET` | Segredo aleatório da sessão (obrigatório) |
| `PROJECTS_DIR` | Diretório raiz dos projetos |
| `COOKIE_SECURE` | `true` quando estiver sob HTTPS |
| `TERMINAL_ENABLED` | Habilitar terminal PTY pelo navegador |

Copie `.env.example` e substitua todos os valores. Nunca versione o arquivo `.env`.

## Escopo do MVP

- Dashboard de CPU, memória, disco, uptime
- Projetos Git: clonar, atualizar via fast-forward, listar
- PM2: listar, iniciar projeto com script do usuário, parar, reiniciar e consultar logs
- Docker Compose: listar containers, subir, parar e consultar logs por projeto
- Terminal PTY opcional via WebSocket (executado como o mesmo usuário sem privilégios do painel)
- Login por senha de variável ambiente com cookie assinado

O gerenciador **não instala automaticamente runtimes**, não faz configuração automática de domínio/Nginx e não oferece isolamento multitenant.

## Segurança

Um painel de deploy com terminal fornece controle efetivo sobre os projetos do usuário Linux que o executa. Use conta dedicada, não execute como root, não adicione o usuário ao grupo docker sem compreender que tal acesso equivale praticamente a root, limite a exposição à rede e mantenha backups. Ações de Git, PM2 e Docker operam somente sobre projetos cadastrados. Repositórios Git são código não confiável: revise-os antes de executar.

## Próximas etapas

Domínios e proxy reverso, logs em streaming, implantação automatizada por tipo de projeto, configuração de variáveis por projeto, backups e serviços systemd.


## Instalação automatizada para teste por IP (HTTP)

**Aviso:** HTTP transmite credenciais e cookies sem criptografia. Use apenas para testes temporários em rede controlada. Para acesso remoto permanente, configure HTTPS e mantenha `TERMINAL_ENABLED=false` até ativar HTTPS.

Este instalador **modifica pacotes e serviços do servidor**. Leia `deploy/install.sh` antes de executá-lo. Em Ubuntu/Debian amd64 ou arm64, com conta sudo:

```bash
git clone https://github.com/MiguelAlmeidaJ/linux_manager.git
cd linux_manager
sudo bash deploy/install.sh
```

Ele instala Node.js 24, PM2, Docker Engine, Compose e as dependências Python, configura usuário `linuxmanager`, systemd e faz o painel escutar em `0.0.0.0:8765`. Credenciais novas ficam somente em `/root/linux-manager-credentials.txt`. Para consultar: `sudo cat /root/linux-manager-credentials.txt`.

Se o UFW estiver **ativo**, libera TCP 8765; se estiver inativo, não o habilita automaticamente para não arriscar bloquear o acesso SSH. Nesse caso, confira as regras do firewall de rede/provedor e, para habilitar UFW manualmente, libere **antes** a porta SSH efetivamente usada pelo servidor. Caso queira restringir a origem, prefira liberar TCP 8765 apenas para seu IP.

Verificações:

```bash
sudo systemctl status linux-manager
sudo systemctl status docker
sudo ss -lntp | grep 8765
curl -I http://127.0.0.1:8765
node -v
pm2 -v
docker compose version
```

Acesse `http://IP-DO-SERVIDOR:8765` se também estiver liberado no firewall do provedor. O grupo `docker` oferece acesso equivalente a root: não instale este painel em servidores com usuários ou aplicações não confiáveis. As aplicações gerenciadas executam com permissões do usuário `linuxmanager`.

Para logs: `sudo journalctl -u linux-manager -f`. Para reiniciar: `sudo systemctl restart linux-manager`. 

## Painel v0.2: aplicações e deploy

O novo layout contém navegação lateral, dashboard com dados em GB e uptime, projetos, PM2, Docker, portas em escuta e terminal.

Cada projeto Git pode ter uma configuração independente:

- **Runtime:** Node.js, Python ou Docker Compose.
- **Porta:** valor de 1024 a 65535; para Node/Python, repassado no ambiente como `PORT`.
- **Comando de instalação:** por exemplo `npm install`, `pnpm install` ou `python3 -m pip install -r requirements.txt`.
- **Comando de build:** por exemplo `npm run build`.
- **Inicialização:** por exemplo `npm run start`, `node server.js`, `python3 main.py` ou `uvicorn main:app --host 0.0.0.0 --port 3000` (a porta deve coincidir com a configurada, caso o comando a fixe).

A ação **Deploy** roda instalação e build (quando configurados) e inicia o processo com PM2; em projetos Docker, chama `docker compose up -d --build`. As ações **Iniciar**, **Parar**, **Reiniciar**, **Git pull** e **Logs** ficam no cartão do projeto. O cadastro e as configurações existentes são preservados entre atualizações.

Os comandos são executados com argumentos, **sem invocar shell**, com a lista de programas permitidos. Isso não substitui revisão de código: scripts npm, Python e Docker podem executar código arbitrário com as permissões da aplicação. Não implante repositórios não confiáveis.

### Atualização da instalação existente

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
exit
sudo systemctl restart linux-manager
sudo systemctl status linux-manager --no-pager
```

Abra o painel e use `Ctrl+Shift+R` para descartar o cache da interface.

**Limitações do MVP:** Não instala dependências específicas de cada repositório automaticamente além dos comandos informados; não verifica conflitos de porta antes de iniciar; não configura Nginx/domínios/SSL; a identificação de status Docker no resumo informa somente o runtime, não o estado de cada container. O deploy é síncrono, portanto builds muito longos podem bloquear aquela requisição. Para produção, uma fila de tarefas e logs progressivos são recomendados.

## Ajustes de monitoramento e PM2 (v0.3)

**CPU:** o painel agora coleta uso da CPU em uma thread de fundo a cada 2 segundos. O cartão principal exibe uma média móvel de até 30 segundos, com a última amostra indicada em texto menor (`Média 30s · atual X%`). Nos primeiros 30 segundos após o restart a média é calculada com as amostras disponíveis, e começa em zero até a primeira amostra.

**PM2:** o serviço systemd e os subprocessos do painel definem explicitamente `HOME=/home/linuxmanager` e `PM2_HOME=/home/linuxmanager/.pm2`. O instalador não executa mais `pm2 -v` como root (essa consulta pode iniciar um daemon do root).

### Atualizar servidor existente

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
exit

# Opcional: atualizar o unit file preservando a porta pública 8765
sudo sed 's@--host 127.0.0.1 --port 8765@--host 0.0.0.0 --port 8765@' \
  /opt/linux-manager/deploy/linux-manager.service | sudo tee /etc/systemd/system/linux-manager.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart linux-manager
```

### Limpeza segura do PM2 do root

O script abaixo **só encerra o daemon PM2 do root se sua lista estiver vazia**, e aborta caso haja processos cadastrados ou erro de leitura. Não apaga projetos nem afeta o daemon do `linuxmanager`.

```bash
cd /opt/linux-manager
sudo bash deploy/cleanup-root-pm2.sh
sudo -iu linuxmanager pm2 list
```

Não execute `sudo pm2 list` depois da limpeza: o próprio comando pode reiniciar um daemon PM2 do root. Se precisar examinar, use `ps -eo user,args | grep '[G]od Daemon'`.

No navegador, utilize Ctrl+Shift+R após atualizar. Confirme que o cartão CPU mostra `Média 30s`.
