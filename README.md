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

## Correção do .env físico para scripts, Prisma e manutenção

O Linux Manager continua mantendo o cofre privado fora do repositório, mas agora **sincroniza automaticamente** um arquivo `.env` real em `/srv/linux-manager/projects/NOME/.env`, sempre que você salva uma variável pelo painel e antes de um deploy. O arquivo recebe permissão `0600`, é criado atomicamente e contém apenas as variáveis cadastradas para o projeto (sem importar valores do `.env.example`).

Para aplicações já configuradas, abra **Aplicações → Variáveis .env → Criar / sincronizar .env do projeto**. O endpoint autenticado `POST /api/projects/{name}/env/sync` requer HTTPS/Traefik autorizado ou túnel SSH, assim como o salvamento de variáveis.

Por segurança, **não sobrescreve arquivos .env preexistentes que não tenham sido criados pelo Linux Manager** e recusa symlinks. Caso o usuário já tenha um `.env` manual, faça backup e migração cuidadosa, sem apagar as configurações existentes. O arquivo deve estar ignorado pelo Git (como no repositório AICorta) e nunca ser enviado ao controle de versão.

Para criar a conta SYSTEM do AICorta depois de sincronizar:

```bash
sudo -iu linuxmanager
cd /srv/linux-manager/projects/AiCorta
pnpm --filter @aicorta/api system:create
```

O script requer senha SYSTEM de 16+ caracteres e URL do banco válida. A geração do arquivo físico não reinicia os processos em execução; para aplicar variáveis à API e ao Next.js, execute novo deploy, especialmente para valores NEXT_PUBLIC_* embutidos no build.

## Editor de texto .env por aplicação

Em **Aplicações → Variáveis .env → Editar .env completo**, o administrador pode abrir um editor de texto com as variáveis já salvas, uma por linha, no formato `NOME=valor`. O editor exibe valores reais, inclusive senhas: só abra num navegador e computador confiáveis, usando HTTPS (ou túnel SSH local). Evite screenshots, compartilhamento de tela, extensões e cópias externas dos valores.

Ao salvar, o Linux Manager valida a sintaxe, impede variáveis duplicadas e nomes reservados, preserva os segredos que não foram alterados, atualiza o cofre privado e sincroniza o `.env` físico do projeto com permissão `0600`. Remover uma linha remove a variável correspondente. O sistema detecta conflitos de edição através de uma revisão SHA-256 e pede recarga quando outra sessão salvou primeiro. Um `.env` preexistente não gerenciado pelo painel nunca é sobrescrito.

A abertura utiliza requisição POST autenticada, com verificação de origem HTTPS/TLS pelo proxy confiável e resposta `Cache-Control: no-store`. Não devolva conteúdo de `.env` em logs, mensagens de erro ou capturas de tela. O navegador recebe os valores apenas após a ação explícita **Editar .env completo**. Após alterar o `.env`, execute um novo deploy para aplicar as mudanças às aplicações em execução, incluindo variáveis `NEXT_PUBLIC_*` compiladas pelo Next.js.

## Interface Frost e remoção segura de aplicações

A interface padrão agora utiliza branco-gelo, superfície branca, navegação clara, controles discretos e acento azul. A página **Aplicações** mostra ações principais (Deploy, Terminal, Variáveis .env, Configurar), agrupando Git, processos e logs em **Mais ações**. O console de resultados permanece recolhido até haver uma operação. Isso altera a apresentação, não os processos ou configurações de deploy existentes.

**Remover aplicação:** em **Aplicações → Mais ações → Remover aplicação**, digite o nome exato da aplicação e confirme. O endpoint POST /api/projects/{name}/remove verifica autenticação, HTTPS via Traefik confiável ou túnel SSH, confirma o nome e recusa exclusão durante deploy. Para projetos PM2, encerra somente os processos associados ao diretório da aplicação e arquiva o código e as variáveis em /srv/linux-manager/archived-projects/NOME-TIMESTAMP-IDENTIFICADOR (permissões restritas), removendo em seguida o registro no painel. O catálogo de bancos, seus dados e volumes Docker **não são alterados**. O arquivo é recuperável manualmente pelo administrador.

Projetos do tipo Docker são **recusados** nesta versão para não deixar serviços/volumes em estados inesperados. Pare-os e investigue-os manualmente antes de implementar o fluxo de remoção de containers. A remoção do painel não exclui repositórios GitHub, bancos ou backups. **Não remova manualmente a pasta de arquivos arquivados antes de avaliar a necessidade de restauração.**

### Atualização

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python3 -m py_compile app.py deploy_jobs.py
exit
sudo systemctl restart linux-manager
```

Atualize o navegador com Ctrl+Shift+R. O deploy e a inicialização das aplicações em PM2 não precisam ser refeitos para aplicar apenas esta atualização visual.

## Gerenciador de Containers — catálogo de serviços compartilhados

A página **Containers** agora exibe um inventário estruturado de todos os containers Docker (nome, imagem, estado, portas e origem) e cartões específicos para **PostgreSQL 17**, **MySQL 8.4** e **Redis 7.4**. Containers que não pertencem ao projeto Compose `linux-manager-databases` são listados **somente para consulta**, sem botões de alteração.

Os serviços gerenciados compartilham o Compose `deploy/databases.compose.yml`, mantendo os volumes e os containers já criados. Portas locais:
- PostgreSQL: `127.0.0.1:5432`
- MySQL: `127.0.0.1:3306`
- Redis: `127.0.0.1:6379`

O botão **Iniciar serviço** executa apenas entradas permitidas do catálogo, verificando previamente conflitos de porta. **Parar serviço** exige confirmação digitando o nome interno; a parada de PostgreSQL/MySQL é bloqueada se houver bancos cadastrados no Linux Manager. Os dados e volumes não são excluídos. **Ver logs** exibe no máximo as últimas 100 linhas, removendo códigos de cores ANSI e mascarando os segredos dos serviços cadastrados.

O Redis recebe uma senha aleatória forte armazenada em `/srv/linux-manager/database-services.env` (modo `0600`), com AOF e volume persistente. Se o arquivo já existir, a nova senha de Redis é adicionada sem trocar as senhas do PostgreSQL e MySQL. Após iniciar Redis, use **Vincular Redis** para configurar `REDIS_URL` em uma aplicação PM2; faça novo deploy para aplicar. O vínculo Docker não está disponível enquanto não houver rede compartilhada segura entre os projetos.

**Avisos:** acesso ao Docker equivale a privilégio elevado no host. A senha Redis fica disponível aos administradores do daemon Docker, pois o container precisa recebê-la. Não exponha o Redis publicamente nem libere a porta 6379 no firewall. Backup, monitoramento de persistência e recuperação ainda precisam ser implementados/testados antes de uso crítico. **SQL Server** não está disponível neste catálogo inicial: exige aceite de licença/EULA, sizing de memória e armazenamento, política de imagens e versão, e um fluxo de provisionamento próprio. Não instalamos imagens arbitrárias pelo painel.

Os consoles da interface agora removem sequências ANSI e mantêm alinhamento das tabelas do PM2 por rolagem horizontal. Logs de deploy novos também desativam cores ANSI na geração.

### Atualização

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python -m py_compile app.py database_manager.py deploy_jobs.py container_manager.py
exit
sudo systemctl restart linux-manager
```

Abra **Containers** e atualize a página com Ctrl+Shift+R. **Não é necessário reiniciar o PostgreSQL ou reimplantar as aplicações existentes** para aplicar a interface. O Redis somente será instalado quando clicar em **Iniciar serviço**.

## v0.9 — Usuários, auditoria e gestão de processos PM2

A autenticação de senha única passa a aceitar usuário/e-mail e senha. Contas e auditoria ficam em SQLite, no diretório privado /srv/linux-manager/auth, com senhas protegidas por scrypt com salt e arquivos restritos ao usuário linuxmanager.

### Primeiro login após a atualização

Na primeira inicialização, se ainda não houver contas, o painel cria uma conta admin (usuário admin) com senha inicial igual à ADMIN_PASSWORD que já existe no /opt/linux-manager/.env. O e-mail inicial é BOOTSTRAP_ADMIN_EMAIL quando configurado ou admin@localhost.invalid. A senha de bootstrap precisa ter pelo menos 12 caracteres. Sessões antigas deixam de funcionar: entre pelo HTTPS usando nome admin e senha anterior. Em Usuários → Editar, substitua imediatamente e-mail e senha por valores fortes e exclusivos. A atualização de ADMIN_PASSWORD no arquivo .env não altera uma conta já criada no SQLite.

### Permissões

O papel user acessa as páginas operacionais atuais, incluindo PM2, aplicações, containers, bancos e terminal quando habilitado. O papel admin tem acesso adicional às páginas Usuários e Auditoria. O backend verifica as permissões além de ocultar a navegação. Administradores podem criar, editar, ativar e inativar contas, alterar senhas e selecionar tipos admin ou user. O painel exige manter pelo menos um administrador ativo e impede autodesativação/rebaixamento.

### Auditoria

Registra timestamp, usuário, ação, recurso, HTTP status e origem das mutações de API, logins e falhas, alterações de contas e abertura/fechamento de sessões de terminal. Não armazena corpos de requisição, valores .env, cookies, senhas ou comandos digitados no shell. Há paginação. Faça backups consistentes do SQLite usando o mecanismo de backup do SQLite, incluindo WAL quando apropriado.

IMPORTANTE: enquanto usuários comuns tiverem acesso ao terminal ou ao Docker com permissões do usuário linuxmanager, eles podem modificar arquivos do sistema e até o banco SQLite. A separação admin/user funciona como controle de acesso do painel, mas NÃO é isolamento seguro diante de shell/Docker privilegiado. Para segurança forte, restrinja shell/Docker aos administradores ou introduza contas/processos Linux separados e isolamento de permissões. A auditoria não é resistente a adulteração por usuários com acesso ao sistema operacional.

### Tela PM2

Agora exibe indicadores de total, online e reinícios, CPU, memória e controles Iniciar/Parar/Reiniciar/Logs por processo. O backend recusa controlar processos que não pertençam a projetos cadastrados.

### Atualização

Faça backup privado do /opt/linux-manager/.env e de /srv/linux-manager antes de migrar. Em seguida, como linuxmanager, execute git pull --ff-only e .venv/bin/python -m py_compile app.py identity_store.py; reinicie sudo systemctl restart linux-manager. Abra pelo HTTPS/Traefik e faça login com admin + senha anterior. Não é necessário reiniciar as aplicações PM2.

## Configurações administrativas — logo e favicon

A página **Configurações**, visível somente para usuários com papel admin, permite personalizar a logo do painel e o favicon do navegador. Há seleção de arquivo, prévia, gravação e restauração individual de cada item. A logo aparece na navegação, na página de login e na prévia de configurações; o favicon aparece na aba do navegador. Usuários comuns visualizam a identidade configurada, mas não podem alterá-la.

Formatos: logo PNG, JPEG ou WebP; favicon PNG, WebP ou ICO. Máximo de 1 MB por imagem. Arquivos SVG e HTML não são aceitos. O backend valida assinatura de arquivo e mantém os nomes controlados, não aceita caminhos enviados pelos usuários. Atualização e restauração exigem sessão de administrador e HTTPS por proxy explicitamente confiável ou túnel SSH local. Os eventos branding.updated e branding.restored são registrados na Auditoria.

Armazenamento privado: /srv/linux-manager/branding/ (diretório 0700, arquivos 0600). Inclua essa pasta em backup seguro. Os recursos são entregues por endpoints restritos a imagens com X-Content-Type-Options: nosniff. Não é necessário reiniciar o Linux Manager após alterar logo ou favicon; a mudança vale para as próximas visitas e também é atualizada na sessão atual.

### Atualização

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python -m py_compile app.py branding.py identity_store.py
exit
sudo systemctl restart linux-manager
```

Após atualizar a página com Ctrl+Shift+R, faça login como admin e abra **Configurações**. Se ainda não tiver uma imagem, o ícone padrão do Linux Manager continuará sendo mostrado.

## Refinamento visual — navegação e cabeçalho

O sidebar utiliza agora ícones SVG vetoriais consistentes, sem depender de fontes externas ou CDN, indicação ativa discreta, melhor hierarquia entre Workspace e Administração e um rodapé de estado compacto. O sidebar tem rolagem própria em telas baixas para não cortar Usuários, Auditoria ou Configurações. No celular, a navegação segue horizontal.

O cabeçalho apresenta o caminho da página atual, servidor conectado, nome e função da conta e um menu de perfil com Configurações (somente admins) e Sair. Ícones dos indicadores do dashboard usam o mesmo sistema vetorial. Nenhuma alteração de banco, sessão ou regras de autorização foi necessária.

Para atualizar: `sudo -iu linuxmanager`, `cd /opt/linux-manager`, `git pull --ff-only`, `exit`, `sudo systemctl restart linux-manager`. Em seguida faça Ctrl+Shift+R no navegador.

## Aplicações com múltiplas portas — visualização e configuração

O cartão de **Aplicações** agora apresenta identidade do projeto, branch, estado PM2, métricas resumidas e a seção **Portas e serviços**. Para projetos PM2, o backend cruza o PID de cada processo e seus processos filhos com as conexões TCP em estado LISTEN do host. Assim, uma aplicação com frontend na porta 3000 e API na porta 3001 pode exibir ambas, identificando o processo responsável quando disponível. Portas não localizadas são sinalizadas como **Não detectada** (sem declarar que estejam liberadas).

Em **Aplicações → Configurar**, o campo **Portas adicionais** aceita números separados por vírgula, por exemplo `3001, 8080`. Esse cadastro só informa ao painel quais portas devem aparecer, mesmo quando seus processos estiverem parados; não altera comandos do servidor, configurações do aplicativo, variáveis de ambiente, firewall ou Traefik. A porta principal continua sendo usada no deploy no modo comando único. Em modo PM2 ecosystem, cada serviço deve configurar sua própria porta, por exemplo `API_PORT=3001` e `WEB_PORT=3000`. O inventário de portas deve ser conferido depois de reiniciar os serviços.

**Segurança:** `127.0.0.1:3001` significa somente local; `0.0.0.0:3001` significa que o processo escuta em todas as interfaces IPv4, não que a porta esteja liberada publicamente. Liberação por firewall e proxy são operações independentes e intencionais. Para Docker, as portas cadastradas aparecem como não detectadas neste painel; consulte a aba Containers para verificar o mapeamento real.

Para atualizar: `sudo -iu linuxmanager`, `cd /opt/linux-manager`, `git pull --ff-only`, `exit`, `sudo systemctl restart linux-manager`, e atualize a página com Ctrl+Shift+R. Esta atualização não reinicia as aplicações PM2.

## Server Manager — múltiplos hosts Linux e Windows (fase 1)

O Linux Manager passa a atuar também como **controlador central** de servidores. O servidor Linux atual permanece como host principal (projetos, Docker, banco e PM2 locais continuam funcionando). A nova aba **Servidores** mostra o host principal e servidores remotos registrados, com estado online/offline, último contato e inventário de processos.

### Windows Server / XAMPP / PM2

O agente de inventário está em `agents/windows_agent.py`. Ele funciona no Windows com **Python 3.11+**, somente biblioteca padrão, sem precisar abrir portas de WinRM, RDP ou HTTP no Windows. Ele envia dados por HTTPS a cada minuto ao controlador:
- processos do PM2 visíveis ao usuário que executa o agente (nome, estado, PID, reinícios e memória, sem expor variáveis de ambiente);
- serviços Windows relevantes para Apache e MariaDB/MySQL;
- processos Apache (httpd.exe) e MariaDB/MySQL (mysqld.exe) do XAMPP mesmo quando iniciados sem registro como serviço;
- versão local do PHP instalado no XAMPP, se o executável estiver em `C:\xampp\php\php.exe` (ou sob `XAMPP_HOME`).

Observe que o XAMPP moderno normalmente distribui **MariaDB**, embora a interface de gerenciamento seja similar à do MySQL. Instalações XAMPP sem serviços Windows são identificadas pela presença dos processos correspondentes, o que é apenas monitoramento inicial. O PM2 é *por usuário*; execute o agente no mesmo contexto Windows que mantém o daemon do PM2. Para inicialização automática dos projetos após reboot do Windows, é necessário configurar serviço/tarefa de inicialização própria do Windows; `pm2 startup` destina-se aos init systems POSIX, não instala um serviço Windows automaticamente. <https://pm2.keymetrics.io/docs/usage/startup/>

### Adicionar Windows Server

1. Entre no painel via HTTPS como admin → **Servidores → Adicionar servidor**; defina nome e Windows Server.
2. Copie o **ID** e o **token privado mostrado uma única vez**. Guarde o token em armazenamento restrito; a API mantém apenas seu SHA-256. O botão *Desativar* revoga o token.
3. Instale Python 3.11+ no Windows e copie `agents/windows_agent.py` para uma pasta privada.
4. Em PowerShell, **na sessão do usuário operacional**, defina os valores (substitua os exemplos pelos reais, sem publicar tokens em tickets ou logs):
   ```powershell
   $env:LM_SERVER_URL = "https://lm.nivel3ti.com.br"
   $env:LM_NODE_ID = "ID_CADASTRADO"
   $env:LM_NODE_TOKEN = "TOKEN_PRIVADO"
   py -3 C:\ServerManager\windows_agent.py
   ```
5. Verifique se o status muda para Online no painel e abra **Ver serviços**. Teste com um processo PM2 e o XAMPP funcionando.
6. Depois de validar, configure execução automática do agente usando tarefa do Windows sob conta dedicada. As variáveis devem ser fornecidas de maneira segura para a conta operacional; **não** coloque o token na linha de comando da tarefa, em scripts públicos ou no repositório. Restrinja leitura dos arquivos/segredos com ACLs do Windows.

### Segurança e limitações da fase 1

Esta versão é **inventário e monitoramento remoto somente leitura**. Ela **não** instala aplicações, executa comandos remotos, altera PM2/XAMPP, cria bancos remotos, configura firewall ou faz deploy no Windows. Esses recursos exigirão um protocolo de comandos tipados, confirmação, isolamento por host, filas de tarefas e auditoria antes de serem liberados. O cadastro de servidores é admin-only e requer sessão segura; os demais usuários autenticados podem consultar o estado dos servidores.

O endpoint `POST /api/agent/nodes/{id}/heartbeat` aceita apenas HTTPS direto ou via proxy explicitamente confiável e exige bearer token único por host. O agente inicia a conexão, então o firewall do Windows pode continuar bloqueando conexões de gerenciamento de entrada. Nunca exponha a porta 8765 diretamente à internet; publique o controlador exclusivamente via Traefik HTTPS/VPN.

O status online significa apenas que chegou um relatório recente (até 180 s); não garante que um projeto específico esteja atendendo requisições. Os valores enviados pelo agente são dados declarados pelo próprio host e não substituem monitoramento externo.

### Upgrade do controlador

```bash
sudo -iu linuxmanager
cd /opt/linux-manager
git pull --ff-only
.venv/bin/python -m py_compile app.py server_registry.py
exit
sudo systemctl restart linux-manager
```

Depois use Ctrl+Shift+R e abra **Servidores**. O arquivo SQLite de registro será criado em `/srv/linux-manager/auth/nodes.sqlite3`; inclua-o nos backups privados da aplicação.

## Windows Server: instalador por download e serviço automático

Na página **Servidores**, um admin pode cadastrar Windows Server e usar **Baixar instalador Windows (.zip)**. O pacote traz scripts PowerShell e Python, configuração e token de autenticação exclusivo para a máquina. Para servidores já cadastrados, **Gerar instalador** rotaciona imediatamente o token anterior (a instalação antiga perde a conexão). O download exige login admin por HTTPS.

**Instalação:** baixe e extraia o ZIP numa pasta privada do Windows. Revise Install-Agent.ps1; abra PowerShell como Administrador na pasta extraída e execute:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Agent.ps1

O instalador detecta Python 3.11+. Se necessário, tenta instalar Python 3.12 usando winget em escopo de máquina (ou orienta instalar manualmente). Instala pywin32, protege os arquivos e a credencial em C:\ProgramData\ServerManager com ACL apenas para SYSTEM e Administradores e registra **Server Manager Agent** como serviço Windows com inicialização Automática. Verifique em services.msc e, após validar, exclua ZIP e pasta extraída, pois contêm o token.

O serviço executa como LocalSystem. PM2 normalmente roda sob outra conta Windows; o instalador tenta registrar pm2_home e o caminho do PM2 do contexto em que foi executado. Se a conta que mantém o daemon for outra, ajuste esses campos no arquivo agent.json e reinicie o serviço; as permissões dos pipes Windows podem exigir rodar o serviço sob a conta operacional do PM2. Essa configuração ainda precisa de validação real em Windows Server e após reboot.

**IP e contexto:** o agente informa IPs locais de suas interfaces, não o IP público/NAT necessariamente. O painel exibe os IPs reportados no seletor do header. Ao selecionar um host remoto, as telas operacionais exclusivas do controlador Linux ficam ocultas e o PM2 apresenta somente processos daquele Windows Server em modo leitura; ao voltar para o servidor local, as operações originais ficam disponíveis. Deploy e controle remoto do PM2 Windows ainda não estão disponíveis.

**Traefik:** configure TRUSTED_PROXY_IPS para o IP exato do proxy. Se a origem HTTPS não puder ser inferida, configure PUBLIC_SERVER_URL=https://lm.nivel3ti.com.br no ambiente do controlador. Não exponha a porta 8765 publicamente. O agente sempre inicia conexões HTTPS para o controlador, sem portas de administração de entrada no Windows.

Para atualizar, faça git pull --ff-only como linuxmanager em /opt/linux-manager, valide a sintaxe de app.py e server_registry.py, reinicie linux-manager pelo systemd e atualize o navegador (Ctrl+Shift+R). O instalador não é assinado; avalie assinatura de código para distribuição corporativa.

## Windows: portas PM2, projetos PHP e bancos XAMPP (inventário somente leitura)

O agente Windows passa a enviar, a cada heartbeat, portas TCP em estado LISTEN associadas ao PID de cada aplicação PM2 e a seus processos filhos. A atribuição é feita pelos vínculos ParentProcessId obtidos via Win32_Process e pelo OwningProcess de Get-NetTCPConnection; não são inventadas portas com base em nomes ou .env. Se o processo não tiver um socket detectável, a coluna fica em branco. O PM2 do controlador Linux também expõe portas com base em psutil.

Em **Processos PM2**, é possível filtrar por parte do nome, PID ou porta e por estado (Online, Stopped/Offline, Errored/Error, Outros). A contagem mostra processos filtrados/total. Os processos do Windows permanecem somente leitura.

Na aba **Servidores**, ao selecionar um Windows Server, há dois inventários novos:

- **Projetos PHP / XAMPP:** diretórios de projetos sob XAMPP_HOME/htdocs, identificados por arquivos PHP, composer.json ou public/index.php, e document roots cadastrados no arquivo apache/conf/extra/httpd-vhosts.conf. O agente não lê conteúdos PHP, .env ou arquivos de senha. Não há execução ou implantação de scripts.
- **Bancos MySQL / MariaDB:** lista somente nomes de bancos que a conta de monitoramento tem permissão de visualizar. Esquemas internos information_schema, performance_schema, mysql e sys são ocultados na interface.

### Configuração de acesso restrito ao MySQL no Windows

O agente **não usa nem solicita credenciais root**. Para habilitar a listagem, crie uma conta específica no MySQL/MariaDB pelo console administrativo local, com somente a permissão de catálogo SHOW DATABASES (e ajuste a origem do usuário conforme a instalação, 127.0.0.1 ou localhost). Exemplo de comandos SQL, executados como administrador do banco:

    CREATE USER 'lm_monitor'@'localhost' IDENTIFIED BY 'SENHA_FORTE_UNICA';
    GRANT SHOW DATABASES ON *.* TO 'lm_monitor'@'localhost';

Crie manualmente C:\ProgramData\ServerManager\mysql-monitor.cnf, com ACL de leitura somente para Administradores e SYSTEM, e conteúdo equivalente a:

    [client]
    host=127.0.0.1
    port=3306
    user=lm_monitor
    password=SENHA_FORTE_UNICA

O agente chama o cliente mysql.exe em XAMPP_HOME/mysql/bin com --defaults-extra-file, --protocol=TCP e SHOW DATABASES, sem colocar a senha na linha de comando nem no relatório HTTPS. Certifique-se de que a conta de monitoramento é reconhecida na origem TCP localhost/127.0.0.1. Se a credencial não existir, o painel exibe **Configuração de monitoramento necessária**; se o banco estiver indisponível, informa a falha genérica, sem publicar senhas. Proteja o arquivo .cnf e não inclua sua senha no Git.

### Atualização de agentes já conectados

Atualize o controlador Linux com git pull --ff-only, valide app.py, server_registry.py e reinicie o serviço linux-manager. Para receber os novos campos num Windows Server **já conectado**, copie a versão atual de agents/windows_agent.py para C:\ProgramData\ServerManager\windows_agent.py (com permissões administrativas), então execute Restart-Service ServerManagerAgent no PowerShell elevado. A credencial do agente continua válida e não precisa ser rotacionada. Alternativamente, use **Gerar instalador**, mas isso revoga o token anterior e exige reinstalar o pacote novo. A primeira versão do instalador/serviço ainda requer testes num Windows Server real.

## Atualizar agente pelo painel + provisionar monitoramento MySQL na instalação

Na página **Servidores**, cada Windows Server ativo tem **Baixar atualização**. O botão gera um ZIP administrativo contendo Update-Agent.ps1, Configure-MySQL.ps1, windows_agent.py e windows_service.py. Diferentemente de **Gerar instalador**, esse pacote NÃO inclui token, NÃO rotaciona credenciais e NÃO altera agent.json. A atualização para temporariamente ServerManagerAgent, salva cópia dos scripts existentes, substitui os arquivos, inicia o serviço e tenta restaurar os scripts anteriores em caso de falha.

No Windows Server, extraia o ZIP numa pasta privada, leia os scripts e execute PowerShell como Administrador:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\Update-Agent.ps1

Para atualizar sem abrir o assistente opcional MySQL, use o parâmetro -SkipMySQL. A conta do agente e o serviço automático continuam existentes. O pacote de atualização só está disponível a contas admin por HTTPS confiável e o download é auditado.

### MySQL / MariaDB do XAMPP

O novo instalador também inclui Configure-MySQL.ps1. Quando mysql.exe do XAMPP estiver presente e mysql-monitor.cnf ainda não existir, o assistente oferece criar a conta de monitoramento automaticamente (perguntando antes). São necessários privilégios administrativos **no banco de dados**: informe usuário (padrão root) e senha do banco LOCALMENTE no PowerShell Windows. Nada é enviado ao painel central.

O script gera uma senha aleatória única de 48 caracteres hexadecimais, cria/atualiza somente lm_monitor@localhost e lm_monitor@127.0.0.1, e concede SHOW DATABASES no catálogo. Esse privilégio permite enumerar bancos, mas não ler tabelas, alterar registros nem criar schemas. O script testa o login da conta criada e grava C:\ProgramData\ServerManager\mysql-monitor.cnf, com ACL restrita a Administradores e SYSTEM. Senhas não são colocadas nos argumentos do processo mysql.exe nem nos logs do agente; um arquivo temporário de credenciais administrativas é protegido e removido após o provisionamento.

Se o XAMPP estiver em outro local, defina XAMPP_HOME como variável de ambiente do PowerShell administrativo antes da instalação. Se mysql.exe não existir, o instalador não tenta instalar o XAMPP ou alterar o MySQL. Se o arquivo mysql-monitor.cnf já existir, é preservado, sem alterar a senha atual. Se a criação de usuário falhar, o agente continua funcionando e a listagem MySQL informa que falta configurar o monitoramento.

Para reexecutar apenas o provisionamento, abra PowerShell como Administrador e execute Configure-MySQL.ps1 a partir do ZIP (ou da versão revisada do repositório), depois Restart-Service ServerManagerAgent. Não forneça root remoto nem abra 3306 na internet. Para grandes ambientes, utilize uma conta local administrada por cofre de segredos e políticas de rotação.

### Atualizar o controlador Linux

    sudo -iu linuxmanager
    cd /opt/linux-manager
    git pull --ff-only
    .venv/bin/python -m py_compile app.py server_registry.py
    exit
    sudo systemctl restart linux-manager

Atualize o navegador com Ctrl+Shift+R e abra Servidores. O instalador e o provisionamento MySQL ainda precisam de validação no Windows Server real antes de serem considerados prontos para produção.

### Diagnóstico: configuração MySQL falhou com código 2

O aviso genérico código 2 não comprova senha errada. O script de configuração agora detecta a porta em XAMPP_HOME\mysql\bin\my.ini, permite escolher outra porta, testa se o serviço aceita TCP em 127.0.0.1 e mostra o estágio da falha e o número de erro MySQL (ex.: 1045, 2002, 2003), sem imprimir stderr bruto nem a senha.

Faça novo download de **Servidores → Baixar atualização**, extraia a nova versão e execute APENAS `powershell -NoProfile -ExecutionPolicy Bypass -File .\Configure-MySQL.ps1` num PowerShell como Administrador. Não é preciso atualizar o serviço Windows novamente nem gerar outro token. O script preserva o mysql-monitor.cnf existente.

Para confirmar a porta do MySQL XAMPP, verifique o valor de port em C:\xampp\mysql\bin\my.ini. Para testar a credencial com prompt seguro, execute `& "C:\xampp\mysql\bin\mysql.exe" --protocol=TCP --host=127.0.0.1 --port=3306 --user=root --password` e entre com a senha quando solicitado (ajuste porta). Não coloque senha na linha de comando. Se a instância usa login somente via socket/named pipe, ajuste os privilégios/conta de conexão TCP local de modo controlado; não exponha o serviço à rede.

### Monitoramento MySQL: conta lm_monitor já criada manualmente

Não recrie a conta nem altere a senha existente. No painel, baixe o ZIP atualizado em Servidores → Baixar atualização, extraia-o em pasta privada e execute SOMENTE `powershell -NoProfile -ExecutionPolicy Bypass -File .\Configure-Existing-MySQL.ps1` em PowerShell elevado. O script pergunta o usuário (padrão lm_monitor), porta (padrão 3306) e senha atual, testa `SHOW DATABASES` pela interface TCP local e somente após confirmação grava `C:\ProgramData\ServerManager\mysql-monitor.cnf` com ACL apenas para SYSTEM e Administradores. Não executa CREATE USER, ALTER USER ou GRANT; não precisa de senha de root.

Depois execute `Restart-Service ServerManagerAgent` e confira os bancos na página Servidores. Opcionalmente, `Configure-MySQL.ps1` agora pergunta primeiro se lm_monitor já existe e delega ao procedimento seguro sem alteração de usuário; criação automática de conta requer confirmação explícita em uma alternativa separada.

Se a conexão falhar com 1045, a credencial e a origem da conta MySQL devem ser conferidas; MariaDB distingue 'localhost' e '127.0.0.1' dependendo do modo de autenticação e resolução. Nenhum arquivo .cnf é publicado se o teste de consulta falhar. A criação manual de usuário no banco não cria, por si só, o arquivo de credenciais local do agente.

## Multi-servidor: visão geral de desempenho, páginas Windows e executável

A tela **Servidores** agora prioriza um resumo de desempenho por host remoto: CPU, RAM usada, RAM total, porcentagem de memória, discos e os processos Windows que mais consomem recursos. O ranking permite ordenar por **CPU** ou **memória**. Os dados são recebidos por HTTPS do agente e são amostras pontuais, não medições históricas ou diagnóstico causal definitivo. Enquanto o agente antigo estiver instalado, as métricas serão apresentadas como indisponíveis; não são inventadas.

**Separação por servidor:** ao escolher um Windows Server no seletor do cabeçalho, o painel oferece páginas independentes **Processos PM2**, **Projetos PHP**, **Bancos MySQL** e **Serviços XAMPP**, todas filtradas pelo ID da máquina. Isso elimina as listas PHP/MySQL duplicadas na visão geral de Servidores. As ações locais continuam bloqueadas no contexto remoto. O painel principal permanece Linux.

### Atualizar o agente Windows para ativar métricas

1. Atualize o controlador em /opt/linux-manager com git pull --ff-only e sudo systemctl restart linux-manager.
2. Baixe **Servidores → Windows → Baixar atualização**. O ZIP agora contém também windows_metrics.py.
3. Extraia em pasta privada, revise Update-Agent.ps1 e execute elevado: `powershell -NoProfile -ExecutionPolicy Bypass -File .\Update-Agent.ps1 -SkipMySQL`.
4. Aguarde o heartbeat (até cerca de 60 segundos) e abra o servidor na página Servidores. CPU/RAM e top processos virão do Windows via Win32_Processor, Win32_OperatingSystem, Win32_PerfFormattedData_PerfProc_Process e Win32_LogicalDisk. São consultas somente leitura.

### ServerManager.exe: build Windows, sem fingir que scripts são executáveis

O repositório inclui o código `agents/server_manager_launcher.py` para um iniciador GUI Windows que exibe uma janela **Server Manager**, valida a presença de enrollment.json e demais scripts ao lado do executável e chama Install-Agent.ps1 com elevação UAC. O binário **ServerManager.exe** é compilado no Windows usando PyInstaller pelo workflow `.github/workflows/build-server-manager-windows.yml` e publicado como artifact `ServerManager-Windows-unsigned` em **GitHub Actions**. O pacote NÃO contém o token dentro do executável: o token fica em enrollment.json ao lado, dentro do ZIP por servidor.

**Atenção:** a definição do workflow foi adicionada, mas a compilação do Windows ainda precisa ser executada e verificada. O painel inclui ServerManager.exe no ZIP de instalação **apenas quando o binário compilado estiver presente em `/opt/linux-manager/agents/bin/ServerManager.exe`**. Sem ele, o ZIP mantém o instalador PowerShell existente. Para disponibilizá-lo pelo painel, obtenha o exe gerado em GitHub Actions, examine seu SHA-256, coloque em `/opt/linux-manager/agents/bin/ServerManager.exe` com propriedade linuxmanager e permissão de leitura e gere um novo ZIP no painel. Não renomeie arquivos PowerShell ou ZIP para .exe. O executável ainda **não é assinado digitalmente** e pode gerar avisos de proteção do Windows; não desative o antivírus globalmente.

**Instalação:** extraia todo o ZIP gerado em pasta privada no Windows. Se ServerManager.exe estiver presente, revise os scripts e execute-o; ele invocará PowerShell com UAC e instalará o serviço com inicialização automática. Remova ZIP e pasta com enrollment.json após finalizar, pois contêm token privado. Use Update-Agent.ps1 para atualizar um agente existente sem trocar token.

**Observação:** o ServerManager.exe aqui instala o **agente do Server Manager no Windows**, não o pacote XAMPP. A instalação automática do XAMPP, Apache, PHP e MySQL como serviços gerenciáveis é um recurso futuro diferente, que demandará um fluxo de instalação explícito e backups.
