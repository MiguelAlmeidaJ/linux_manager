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
