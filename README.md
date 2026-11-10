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

## v0.4 — Deploy em segundo plano, variáveis e Git automático

Na aba **Aplicações**, configure runtime, porta e comandos. Em **Variáveis .env**, cadastre chave e valor; os valores ficam em `/srv/linux-manager/env/NOME.json`, com permissão 0600, **fora do repositório Git**. A interface mostra apenas nomes das chaves, nunca os valores. O sistema injeta essas variáveis no processo durante instalação, build e deploy (e `PORT` para Node/Python). Alterações de variáveis exigem novo deploy; para Docker Compose, variáveis são passadas no ambiente do comando Compose.

**Deploy**, **Git pull** e **Atualizar + deploy** executam como trabalhos de fundo. O painel consulta `/api/projects/NOME/job` a cada 2 segundos e exibe o log incremental da tarefa. Os logs são temporários e ficam apenas na memória do processo; após reiniciar o serviço desaparecem. Saídas das ferramentas podem revelar dados sensíveis gerados pelo próprio projeto: evite comandos que imprimam segredos. Valores das variáveis cadastradas são mascarados nos logs quando possível.

**Atualização automática pelo Git** é opt-in, por projeto, e fica desabilitada por padrão. Quando ativada, o Linux Manager verifica `origin/branch` aproximadamente a cada 3 minutos. Se encontrar commit diferente, faz `git pull --ff-only` e um novo deploy. Credenciais Git (inclusive SSH) devem estar acessíveis ao usuário `linuxmanager`. Não ative em projetos que não tenham sido testados; um commit pode reiniciar a aplicação.

As operações usam exclusivamente o usuário Linux `linuxmanager` e não concedem privilégios adicionais aos projetos. O Docker continua sendo altamente privilegiado via grupo `docker`.

### Atualização instalada

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python3 -m py_compile app.py deploy_jobs.py
exit
sudo systemctl restart linux-manager
sudo journalctl -u linux-manager -n 30 --no-pager
```

Abra o painel e faça `Ctrl+Shift+R`. A configuração anterior dos projetos é mantida. O serviço deve permanecer com **um worker** de Uvicorn, pois jobs e monitor automático são mantidos na memória.

### Limitações / próximos passos

O fluxo atual não oferece rollback automático nem publicação HTTPS/domínio. Logs incrementais dependem de a ferramenta utilizada emitir saída continuamente (algumas ferramentas fazem buffering). O job em andamento não é retomado após reinício do Linux Manager. O monitor Git usa polling em vez de webhooks. O campo de porta não reconfigura a porta interna de um Compose; essa configuração permanece no `compose.yaml` do projeto. O comando de inicialização Python/Node deve respeitar a variável `PORT` ou informar a porta no próprio comando.

## v0.5 — GitHub privado com SSH e atualização automática da main

O painel agora oferece **Aplicações → GitHub · Chave SSH de deploy**:

1. Clique em **Gerar chave SSH**. O par Ed25519 é criado localmente no usuário `linuxmanager`, em `/home/linuxmanager/.ssh/linux_manager_github_ed25519`. O servidor nunca entrega a chave privada por API.
2. Copie a chave **pública** exibida. No GitHub, acesse **Settings → Deploy keys → Add deploy key** do repositório privado. Não marque **Allow write access**.
3. Para repositórios adicionais, atenção: **o GitHub não permite reutilizar a mesma deploy key em vários repositórios**. Nesse cenário, cadastre a chave pública em uma conta de máquina dedicada, com acesso de leitura apenas aos repositórios necessários, ou implemente chaves por projeto. A chave pública também pode ser cadastrada em **GitHub → Settings → SSH and GPG keys** da conta autorizada, mas isso concede os acessos dessa conta à chave.
4. Teste o endereço SSH `git@github.com:ORGANIZACAO/REPOSITORIO.git` na tela. Depois cadastre a aplicação utilizando o mesmo endereço e configure **branch `main`**.
5. Configure runtime, porta e comandos de deploy. Ative **Atualização automática pelo Git** na aplicação. O servidor faz polling do GitHub aproximadamente a cada 3 minutos; se houver commit novo na branch configurada, executa `pull --ff-only`, instalação, build e reinicialização. O monitoramento também funciona em repositórios privados autorizados.

Para autenticar o host remoto, o Linux Manager usa a chave pública **Ed25519 oficial do GitHub fixada** em um arquivo known_hosts próprio, com `StrictHostKeyChecking=yes`. A impressão digital oficial é `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`.

**Atualização em servidor existente**:

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python3 -m py_compile app.py deploy_jobs.py github_ssh.py
exit
sudo systemctl restart linux-manager
```

Depois atualize o navegador com Ctrl+Shift+R. Em projetos privados, a chave precisa primeiro ser autorizada no GitHub; gerar a chave sozinho **não** concede acesso. Portas SSH de saída (22/TCP) precisam estar liberadas, salvo uso de outra configuração externa de SSH.

**Segurança:** por estar atualmente exposto em HTTP, o painel não deve ser acessado na internet aberta. Uma sessão interceptada permite executar deploys e administrar chaves públicas. Para utilização permanente, adote HTTPS, restrinja acesso à rede/VPN e mantenha a chave privada protegida.

## v0.6 — Terminal por aplicação e deploy de monorepos

Cada projeto cadastrado possui um botão **Terminal** que abre uma shell Linux como usuário `linuxmanager`, **já no diretório daquele projeto** (ex.: `/srv/linux-manager/projects/aicorta`). O terminal não isola comandos por projeto; ele tem as permissões do usuário `linuxmanager`. Ao clicar em Desconectar, a sessão é encerrada.

**Segurança:** o terminal é desabilitado por padrão e exige `TERMINAL_ENABLED=true`. Para impedir exposição de shell e credenciais em HTTP, WebSockets de terminal são permitidos **somente por HTTPS ou pelo endereço de loopback localhost/127.0.0.1** (idealmente com túnel SSH). Se usar uma conexão HTTP em IP de LAN, o terminal permanecerá bloqueado mesmo com a flag ligada.

Exemplo de acesso seguro para testes por túnel (rodar no seu computador):

```bash
ssh -L 8765:127.0.0.1:8765 USUARIO_SSH@IP_DO_SERVIDOR
```

Em outra janela do navegador, abra `http://localhost:8765` e faça login no painel. Configure `TERMINAL_ENABLED=true` no arquivo `/opt/linux-manager/.env` e reinicie `sudo systemctl restart linux-manager`.

Na configuração de cada projeto, você agora pode selecionar **PM2 ecosystem.config.cjs** como modo de inicialização, ideal para repositórios que criam múltiplos processos (API + Web). O deploy executa os campos de Instalação, Preparação e Build em sequência, e depois executa `pm2 startOrReload ecosystem.config.cjs --env production --update-env`. A preparação aceita **um comando por linha** (sem shell), incluindo `docker compose up -d postgres` para projetos conhecidos.

### Exemplo: AICorta

Repositório: `git@github.com:MiguelAlmeidaJ/aicorta.git` ou URL HTTPS; branch `main`.

- Runtime: Node.js
- Modo: PM2 ecosystem.config.cjs
- Instalação: `pnpm install --frozen-lockfile`
- Preparação, cada comando em linha separada:
  - `docker compose up -d postgres`
  - `pnpm db:generate`
  - `pnpm --filter @aicorta/api exec prisma migrate deploy`
- Build: `pnpm build`
- Comando de inicialização: não utilizado no modo ecosystem

O projeto AICorta usa pnpm 10.18.0 e Node.js >=22. Em distribuições sem pnpm global, instale-o como administrador do servidor antes de executar o deploy. As variáveis necessárias no painel são `DATABASE_URL`, `JWT_SECRET`, `WEB_ORIGIN`, `NEXT_PUBLIC_API_URL`, `API_PORT`, `API_HOST`, `WEB_PORT` e `WEB_HOST`. Para acessar o frontend por IP em testes, configure as portas e endereços correspondentes antes do build. As variáveis `NEXT_PUBLIC_*` são incorporadas durante `pnpm build`.

**Importante sobre o Docker Compose do AICorta:** a configuração existente publica PostgreSQL em `0.0.0.0:5432` e usa senha de desenvolvimento. **Não exponha esse Compose diretamente à internet**. Altere a configuração para bind apenas em `127.0.0.1`, use senha forte e atualize a URL do banco antes de uso público. Não execute esse banco no servidor que já tenha PostgreSQL utilizando a porta 5432 sem ajustar o mapeamento.

### Atualização

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python3 -m py_compile app.py deploy_jobs.py github_ssh.py
exit
sudo systemctl restart linux-manager
```

Recarregue o browser com Ctrl+Shift+R. Os processos PM2 são iniciados pelo mesmo usuário `linuxmanager`. O modo ecosystem utiliza nomes definidos pelo próprio repositório, e o status agrega os processos associados ao diretório do projeto.

## v0.7 — Bancos de Dados gerenciados

Nova aba **Bancos de Dados** para gerenciar um PostgreSQL 17 e um MySQL 8.4 centralizados em Docker Compose, criar bancos e usuários, conceder permissões somente leitura ou leitura/escrita e vincular uma DATABASE_URL à aplicação PM2.

**Isolamento de portas:** o PostgreSQL é publicado apenas em 127.0.0.1:5432 e o MySQL apenas em 127.0.0.1:3306. Cada um é um serviço compartilhado, com vários bancos isolados logicamente. Se uma porta já estiver ocupada por um serviço não gerenciado, o painel se recusa a iniciar outro banco nessa porta, sem interromper os serviços existentes.

**Segredos:** senhas administrativas são geradas automaticamente no primeiro uso, no arquivo /srv/linux-manager/database-services.env (modo 0600). As credenciais de bancos e usuários ficam em /srv/linux-manager/managed-databases.json (0600). Não copie esses arquivos para repositórios e inclua-os nos backups seguros. As respostas da API não retornam senhas.

**Criação segura:** operações que criam serviços, bancos, usuários ou vínculos exigem HTTPS ou um túnel SSH local, não o IP HTTP da rede. Para testar:

    ssh -L 8765:127.0.0.1:8765 USUARIO_SSH@IP_DO_SERVIDOR

Depois entre no painel em http://localhost:8765. Não habilite 5432 ou 3306 no firewall externo.

**Vinculação:** no formulário de criação ou em um banco já existente, selecione uma aplicação PM2 e o usuário. O painel grava DATABASE_URL no cofre de variáveis da aplicação; faça novo deploy para aplicar. O PostgreSQL utiliza o schema public por padrão. Usuários adicionais do PostgreSQL têm direitos nas tabelas existentes e nas futuras tabelas criadas pelo proprietário no schema public. Direitos de outros schemas devem ser configurados separadamente.

**Atenção a projetos Docker:** a conexão 127.0.0.1 dentro de um container não chega ao banco no host. O vínculo automático de apps Docker foi propositalmente bloqueado nesta primeira etapa. Precisamos implementar rede interna para containers antes de usá-lo nesses apps. Também remova os containers locais de PostgreSQL/MySQL dos projetos quando for migrá-los para os bancos compartilhados.

**Migração:** criar um banco central não importa dados existentes. Faça backup, restaure, valide dados e conexão, atualize as variáveis e só então desative os serviços antigos. Não remova volumes sem restauração testada.

### Atualização do servidor

    sudo -iu linuxmanager
    cd /opt/linux-manager
    git pull --ff-only
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m py_compile app.py deploy_jobs.py database_manager.py
    exit
    sudo systemctl restart linux-manager

Recarregue o navegador com Ctrl+Shift+R, abra Bancos de Dados e inicie o serviço desejado.

**Limitações de segurança:** o MVP ainda não inclui backup/restauração automáticos, rotação de senha, TLS de banco, alta disponibilidade, migração automática ou isolamento entre aplicações não confiáveis. Não use bancos de produção sem configurar e testar backups externos. Acesso ao usuário linuxmanager ou ao daemon Docker dá acesso privilegiado aos dados; proteja o host e configure HTTPS para o painel. Não há endpoints de exclusão de bancos nesta fase para reduzir risco de perda de dados.


## v0.7.1 — HTTPS pelo Traefik

Quando o Traefik termina o TLS, o navegador usa HTTPS, mas o Uvicorn pode receber HTTP internamente. Para operações sensíveis de bancos, o painel agora só aceita o cabeçalho X-Forwarded-Proto=https quando o endereço de origem da conexão TCP é explicitamente confiável.

**Configuração no servidor**:

1. Identifique o **IP de origem do proxy Traefik como visto pelo Linux Manager**. Após acessar o painel pelo domínio, rode `sudo journalctl -u linux-manager -n 40 --no-pager` e confira o endereço mostrado nas requisições. Não confunda com o IP público do navegador.
2. Edite `/opt/linux-manager/.env`: defina `TRUSTED_PROXY_IPS=IP_DO_TRAEFIK/32` (IPv4) ou `.../128` (IPv6), usando IP real e estável da origem do Traefik, e `COOKIE_SECURE=true`.
3. Garanta que o Traefik envie e normalize `X-Forwarded-Proto: https`, preserve o cabeçalho Host original e encaminhe o Origin original; configure o domínio HTTPS e o entrypoint websecure.
4. Reinicie o serviço com `sudo systemctl restart linux-manager`. Entre novamente pelo endereço HTTPS e teste a criação do serviço.

**Não configure** `TRUSTED_PROXY_IPS=0.0.0.0/0`. Não permita acesso geral à porta HTTP interna 8765: restrinja-a ao Traefik e, se necessário, ao loopback para acesso via SSH. Em versões futuras, prefira Unix socket ou uma rede interna exclusiva do proxy.

O login agora deve utilizar cookie Secure no domínio HTTPS. Caso o backend ainda retorne 403, confirme primeiro o IP que ele realmente recebe, os cabeçalhos encaminhados e se `TRUSTED_PROXY_IPS` foi carregado pelo serviço systemd. Não desabilite a verificação de HTTPS.

## Variáveis do .env.example no painel

Na aba Aplicações, abra Variáveis .env e clique em **Carregar variáveis do .env.example**. A API lê somente os nomes de variáveis não comentadas no arquivo `.env.example` do projeto. A lista mostra quais chaves já possuem valor configurado e quais estão pendentes. Clique na chave para preencher seu valor individualmente.

O procedimento não sobrescreve variáveis existentes, não importa valores de exemplo, não cria um `.env` físico e não envia segredos do cofre para o navegador. As variáveis cadastradas permanecem armazenadas no arquivo privado do Linux Manager, sendo injetadas no processo durante o deploy. Para aplicar novas variáveis é necessário executar o deploy novamente.

Atualização: `sudo -iu linuxmanager`, `cd /opt/linux-manager`, `git pull --ff-only`, `exit`, `sudo systemctl restart linux-manager`.
