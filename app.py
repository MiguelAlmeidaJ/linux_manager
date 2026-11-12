import asyncio
from collections import deque
from threading import Event, Lock, Thread
import hmac
import ipaddress
import json
import io
import zipfile
import os
import pty
import re
import secrets
import signal
import shutil
import threading
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
import deploy_jobs as dj
import github_ssh as ghssh
import database_manager as dbm
import container_manager as cm
import identity_store as identities
import branding
import server_registry as nodes

ROOT = Path(os.environ.get("PROJECTS_DIR", "/srv/linux-manager/projects")).expanduser().resolve()
ROOT.mkdir(parents=True, exist_ok=True)
DATA_FILE = ROOT.parent / "linux-manager-projects.json"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("SESSION_SECRET", "")
if not SECRET or len(SECRET) < 32:
    raise RuntimeError("Configure SESSION_SECRET (mín. 32 caracteres).")
# ADMIN_PASSWORD is only used to seed the initial local admin, once.
identities.init(PASSWORD)
serializer = URLSafeTimedSerializer(SECRET, salt="linux-manager-session-v1")
SECURE_COOKIE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
TERMINAL_ENABLED = os.environ.get("TERMINAL_ENABLED", "false").lower() == "true"
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
app = FastAPI(title="Linux Manager", docs_url=None, redoc_url=None, openapi_url=None)

# Coleta passiva a cada 2 s; a requisição HTTP não executa mais uma
# medição bloqueante de 100 ms, que era sensível a picos muito curtos.
CPU_SAMPLES = deque(maxlen=30)  # últimos 60 segundos
CPU_LOCK = Lock()
CPU_STOP = Event()
CPU_THREAD = None

def cpu_sampler():
    psutil.cpu_percent(interval=None)  # inicializa contador do processo de coleta
    while not CPU_STOP.wait(2):
        value = psutil.cpu_percent(interval=None)
        with CPU_LOCK:
            CPU_SAMPLES.append((time.monotonic(), value))

@app.on_event("startup")
def start_cpu_sampler():
    global CPU_THREAD
    CPU_STOP.clear()
    CPU_THREAD = Thread(target=cpu_sampler, name="cpu-sampler", daemon=True)
    CPU_THREAD.start()

@app.on_event("shutdown")
def stop_cpu_sampler():
    CPU_STOP.set()
    if CPU_THREAD:
        CPU_THREAD.join(timeout=3)

def cpu_metrics():
    now = time.monotonic()
    with CPU_LOCK:
        samples = list(CPU_SAMPLES)
    recent = [value for stamp, value in samples if now - stamp <= 30]
    latest = samples[-1][1] if samples else 0.0
    return {
        "cpu": round(sum(recent) / len(recent), 1) if recent else 0.0,
        "cpu_instant": round(latest, 1),
        "cpu_samples": len(recent),
        "cpu_window_seconds": 30,
    }

def subprocess_env():
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "SESSION_SECRET")}
    # O painel roda como linuxmanager; evite que variáveis vindas de sudo/root
    # conectem o CLI do PM2 ao daemon de outro usuário.
    home = str(Path.home())
    env["HOME"] = home
    env["PM2_HOME"] = str(Path(home) / ".pm2")
    return env


def session_actor(token):
    try:
        identity = serializer.loads(token or "", max_age=12 * 3600)
        if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
            return None
        row = identities.get_user(identity["id"])
        if not row or not row["active"] or row["session_version"] != identity.get("version"):
            return None
        return row
    except (BadSignature, ValueError, TypeError):
        return None


def authorized(token):
    return session_actor(token) is not None


def assert_auth(request: Request):
    actor = session_actor(request.cookies.get("lm_session"))
    if not actor:
        raise HTTPException(401, "Não autenticado")
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        parsed = urlparse(origin)
        if parsed.netloc != host or parsed.scheme not in ("http", "https"):
            raise HTTPException(403, "Origem inválida")
    return actor


def assert_admin(request: Request):
    actor = assert_auth(request)
    if actor["role"] != "admin":
        raise HTTPException(403, "Acesso restrito a administradores")
    return actor

def records():
    try:
        return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
    except (ValueError, OSError):
        raise HTTPException(500, "Cadastro de projetos inválido")

def save(data):
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(DATA_FILE)

def get_project(name):
    if not NAME.fullmatch(name):
        raise HTTPException(400, "Nome inválido")
    data = records()
    if name not in data:
        raise HTTPException(404, "Projeto não encontrado")
    path = (ROOT / name).resolve()
    if path.parent != ROOT or not path.is_dir():
        raise HTTPException(400, "Diretório inválido")
    return data[name], path

def command(args, cwd=None, timeout=60, env_override=None):
    try:
        result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout, env=env_override or {**subprocess_env(), "GIT_TERMINAL_PROMPT": "0"})
    except FileNotFoundError:
        raise HTTPException(400, f"Ferramenta não instalada: {args[0]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Operação excedeu o limite de tempo")
    output = (result.stdout + "\n" + result.stderr)[-12000:]
    if result.returncode != 0:
        raise HTTPException(400, output.strip() or f"Comando falhou ({result.returncode})")
    return output.strip()

class Login(BaseModel):
    username: str
    password: str

class Project(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    repo: str = Field(min_length=5, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=120)

class Start(BaseModel):
    script: str = Field(default="index.js", min_length=1, max_length=240)

@app.post("/api/login")
def login(payload: Login, request: Request):
    require_secure_database_admin(request)
    actor = identities.authenticate(payload.username, payload.password, request.client.host if request.client else "")
    identities.log(actor["id"], actor["username"], "auth.login", "session", 200,
                   request.client.host if request.client else "")
    response = JSONResponse({"ok": True, "username": actor["username"], "role": actor["role"]})
    response.set_cookie("lm_session", serializer.dumps({"id": actor["id"], "version": actor["session_version"]}),
                        httponly=True, samesite="strict", secure=SECURE_COOKIE, max_age=12 * 3600, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/api/logout")
def logout(request: Request):
    actor = assert_auth(request)
    identities.log(actor["id"], actor["username"], "auth.logout", "session", 200,
                   request.client.host if request.client else "")
    response = JSONResponse({"ok": True})
    response.delete_cookie("lm_session", path="/")
    return response

@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")

@app.get("/api/me")
def me(request: Request):
    assert_auth(request)
    actor = session_actor(request.cookies.get("lm_session"))
    return {"ok": True, "terminal": TERMINAL_ENABLED, "username": actor["username"],
            "email": actor["email"], "role": actor["role"], "user_id": actor["id"]}

@app.get("/api/users")
def list_panel_users(request: Request):
    assert_admin(request)
    return identities.list_users()


@app.post("/api/users")
def create_panel_user(payload: identities.NewUser, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = identities.create_user(payload)
    identities.log(actor["id"], actor["username"], "user.created",
                   "user:" + str(result["id"]), 201, request.client.host if request.client else "")
    return result


@app.put("/api/users/{uid}")
def update_panel_user(uid: int, payload: identities.EditUser, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = identities.edit_user(uid, payload, actor["id"])
    identities.log(actor["id"], actor["username"], "user.updated",
                   "user:" + str(uid), 200, request.client.host if request.client else "")
    return result


@app.get("/api/audit")
def panel_audit(request: Request, limit: int = 100, offset: int = 0):
    assert_admin(request)
    return identities.list_audit(limit, offset)


def audit_action(method: str, path: str) -> str:
    """Categorize auditable mutations without reading request bodies or secrets."""
    if path.startswith("/api/pm2/processes/"):
        return "pm2." + path.rsplit("/", 1)[-1]
    if path.startswith("/api/projects/"):
        operation = path.rsplit("/", 1)[-1]
        if "/env/document" in path:
            return "environment.save" if method == "PUT" else "environment.open"
        if operation in ("deploy", "update-deploy", "pull"):
            return "project." + operation
        if operation == "remove":
            return "project.remove"
        if operation in ("config", "env", "sync"):
            return "project." + operation
        if "/process/" in path:
            return "process." + operation
    if path.startswith("/api/containers/"):
        return "service." + path.rsplit("/", 1)[-1]
    if path.startswith("/api/databases/"):
        return "database." + path.rsplit("/", 1)[-1]
    if path == "/api/projects":
        return "project.create"
    return "api." + method.lower()


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    # Every authenticated state-changing API request gets metadata-only audit.
    # Login/logout and user changes already use explicit events.
    path = request.url.path
    tracked = (path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE")
               and path not in ("/api/login", "/api/logout", "/api/users")
               and not path.startswith("/api/users/"))
    actor = session_actor(request.cookies.get("lm_session")) if tracked else None
    try:
        response = await call_next(request)
    except Exception:
        if actor:
            identities.log(actor["id"], actor["username"], audit_action(request.method, path),
                           path, 500, request.client.host if request.client else "")
        raise
    if actor:
        identities.log(actor["id"], actor["username"], audit_action(request.method, path),
                       path, response.status_code, request.client.host if request.client else "")
    return response


@app.get("/api/branding")
def branding_settings():
    # Only public information: the optional image URLs (login must display branding).
    return JSONResponse(branding.metadata(), headers={"Cache-Control": "no-store"})


@app.get("/branding/{kind}")
def branding_image(kind: str):
    path, mime = branding.asset(kind)
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "public, max-age=60",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.put("/api/admin/branding/{kind}")
def branding_upload(kind: str, payload: branding.AssetUpload, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = branding.save(kind, payload)
    identities.log(actor["id"], actor["username"], "branding.updated",
                   "branding:" + kind, 200, request.client.host if request.client else "")
    return result


@app.delete("/api/admin/branding/{kind}")
def branding_reset(kind: str, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = branding.reset(kind)
    identities.log(actor["id"], actor["username"], "branding.restored",
                   "branding:" + kind, 200, request.client.host if request.client else "")
    return result


@app.get("/api/servers/local")
def local_server_network(request: Request):
    assert_auth(request)
    addresses = []
    try:
        for interface_addresses in psutil.net_if_addrs().values():
            for value in interface_addresses:
                address = value.address.split("%")[0]
                try:
                    ip = ipaddress.ip_address(address)
                    if not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast:
                        if str(ip) not in addresses:
                            addresses.append(str(ip))
                except ValueError:
                    continue
    except (OSError, psutil.Error):
        pass
    return {"hostname": os.uname().nodename, "addresses": addresses[:16]}


@app.get("/api/servers")
def server_list(request: Request):
    assert_auth(request)
    return nodes.listing()


@app.get("/api/servers/{node_id}")
def server_details(node_id: int, request: Request):
    assert_auth(request)
    return nodes.detail(node_id)


@app.post("/api/servers")
def server_create(payload: nodes.NodeCreate, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = nodes.create(payload)
    identities.log(actor["id"], actor["username"], "server.created",
                   "server:" + str(result["id"]), 201, request.client.host if request.client else "")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


class AgentInstallerRequest(BaseModel):
    token: str = Field(min_length=40, max_length=160)


@app.post("/api/servers/{node_id}/installer")
def server_installer(node_id: int, payload: AgentInstallerRequest, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    if nodes.verify_token(node_id, payload.token) != "windows":
        raise HTTPException(400, "Instalador disponível apenas para Windows Server")
    # Use source files from this local, version-controlled application.
    src = Path(__file__).resolve().parent / "agents"
    files = ("Install-Agent.ps1", "Configure-MySQL.ps1", "Configure-Existing-MySQL.ps1", "windows_agent.py", "windows_metrics.py", "windows_service.py")
    for filename in files:
        if not (src / filename).is_file() or (src / filename).is_symlink():
            raise HTTPException(503, "Arquivos de instalação não encontrados")
    base_url = os.environ.get("PUBLIC_SERVER_URL", "").strip().rstrip("/")
    origin = urlparse(request.headers.get("origin", ""))
    if not base_url and origin.scheme == "https" and origin.netloc == request.headers.get("host"):
        base_url = origin.scheme + "://" + origin.netloc
    if not base_url.startswith("https://"):
        raise HTTPException(400, "Configure PUBLIC_SERVER_URL com o domínio HTTPS do painel")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for filename in files:
            target.write(src / filename, filename)
        # Optional Windows CI artifact installed by the operator under agents/bin.
        # Do not manufacture or rename a ZIP or PowerShell file to .exe.
        executable = src / "bin" / "ServerManager.exe"
        if executable.is_file() and not executable.is_symlink():
            target.write(executable, "ServerManager.exe")
        target.writestr("enrollment.json", json.dumps({
            "server_url": base_url, "node_id": node_id, "token": payload.token
        }, ensure_ascii=False))
        target.writestr("LEIA-ME.txt",
            "Server Manager - agente Windows\n"
            "PACOTE CONFIDENCIAL: contém token de autenticação.\n"
            "1. Extraia o ZIP em pasta privada no Windows Server.\n"
            "2. Se ServerManager.exe estiver no ZIP, execute-o como Administrador.\n"
            "   Caso contrario, revise Install-Agent.ps1 e execute via PowerShell.\n"
            "3. Para executar: powershell -NoProfile -ExecutionPolicy Bypass -File .\\Install-Agent.ps1\n"
            "4. Confirme em services.msc: Server Manager Agent (Automático).\n"
            "5. Apague o ZIP e a pasta extraída após instalar.\n"
            "6. Se necessário, ajuste pm2_home em C:\\ProgramData\\ServerManager\\agent.json.\n")
    identities.log(actor["id"], actor["username"], "server.installer.download",
                   "server:" + str(node_id), 200, request.client.host if request.client else "")
    return Response(archive.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="server-manager-windows-agent.zip"',
        "Cache-Control": "no-store, private", "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff"
    })


@app.post("/api/servers/{node_id}/agent-update")
def server_agent_update_download(node_id: int, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    node = nodes.detail(node_id)
    if node["os_type"] != "windows" or not node["enabled"]:
        raise HTTPException(404, "Servidor Windows ativo não encontrado")
    src = Path(__file__).resolve().parent / "agents"
    files = ("Update-Agent.ps1", "Configure-MySQL.ps1",
             "Configure-Existing-MySQL.ps1", "windows_agent.py", "windows_metrics.py", "windows_service.py")
    for filename in files:
        if not (src / filename).is_file() or (src / filename).is_symlink():
            raise HTTPException(503, "Arquivos de atualização do agente indisponíveis")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for filename in files:
            bundle.write(src / filename, filename)
        bundle.writestr("LEIA-ME.txt",
            "Server Manager - Atualizacao do agente Windows\\n"
            "Este ZIP nao contem token, senha ou outras credenciais.\\n"
            "1. Extraia o pacote em uma pasta temporaria privada no Windows Server.\\n"
            "2. Execute em PowerShell elevado: .\\\\Update-Agent.ps1\\n"
            "3. A atualizacao preserva agent.json e o token atual; reinicia o servico.\\n"
            "4. Opcionalmente, configure a conta MySQL de monitoramento quando solicitado.\\n"
            "5. Apague a pasta extraida apos a operacao.\\n")
    identities.log(actor["id"], actor["username"], "server.agent.update.download",
                   "server:" + str(node_id), 200, request.client.host if request.client else "")
    return Response(archive.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="server-manager-windows-update.zip"',
        "Cache-Control": "no-store, private", "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff"
    })


@app.post("/api/servers/{node_id}/rotate-token")
def server_rotate_token(node_id: int, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = nodes.rotate_token(node_id)
    identities.log(actor["id"], actor["username"], "server.token.rotated",
                   "server:" + str(node_id), 200, request.client.host if request.client else "")
    return JSONResponse(result, headers={"Cache-Control": "no-store, private"})


@app.post("/api/servers/{node_id}/disable")
def server_disable(node_id: int, request: Request):
    actor = assert_admin(request)
    require_secure_database_admin(request)
    result = nodes.disable(node_id)
    identities.log(actor["id"], actor["username"], "server.disabled",
                   "server:" + str(node_id), 200, request.client.host if request.client else "")
    return result


@app.post("/api/agent/nodes/{node_id}/heartbeat")
def agent_heartbeat(node_id: int, payload: nodes.Heartbeat, request: Request):
    # Agents never use browser sessions. Requires valid per-node token AND TLS.
    # Do not trust X-Forwarded-Proto from an untrusted client.
    if not (request.url.scheme == "https" or (_trusted_proxy(request.client.host if request.client else "") and request.headers.get("x-forwarded-proto", "").lower() == "https" and bool(request.headers.get("host")) and request.headers.get("x-forwarded-host", request.headers.get("host")) == request.headers.get("host"))):
        raise HTTPException(403, "Agente exige HTTPS via proxy confiável")
    result = nodes.heartbeat(node_id, request.headers.get("authorization", ""), payload)
    return result


@app.get("/api/stats")
def stats(request: Request):
    assert_auth(request)
    disk = psutil.disk_usage("/")
    mem = psutil.virtual_memory()
    return {
        **cpu_metrics(), "memory": mem.percent,
        "memory_used": mem.used, "memory_total": mem.total,
        "disk": disk.percent, "disk_used": disk.used, "disk_total": disk.total,
        "uptime": int(time.time() - psutil.boot_time()),
        "hostname": os.uname().nodename,
        "projects": len(records()),
    }

@app.get("/api/projects")
def projects(request: Request):
    assert_auth(request)
    return [{"name": name, **entry} for name, entry in records().items()]

@app.post("/api/projects")
def create_project(payload: Project, request: Request):
    assert_auth(request)
    if not NAME.fullmatch(payload.name):
        raise HTTPException(400, "Use apenas letras, números, _ e -")
    if payload.repo.startswith("-") or not (
        payload.repo.startswith("https://") or
        re.fullmatch(r"git@[A-Za-z0-9._-]+:[A-Za-z0-9._/-]+(?:\.git)?", payload.repo)
    ):
        raise HTTPException(400, "Use URL HTTPS ou SSH de repositório Git")
    if payload.branch.startswith("-") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", payload.branch) or ".." in payload.branch:
        raise HTTPException(400, "Branch inválida")
    data = records()
    path = ROOT / payload.name
    if payload.name in data or path.exists():
        raise HTTPException(409, "Projeto já existe")
    command(["git", "clone", "--depth", "1", "--branch", payload.branch, "--", payload.repo, str(path)], timeout=180, env_override=ghssh.git_environment(subprocess_env()))
    data[payload.name] = {"repo": payload.repo, "branch": payload.branch}
    save(data)
    return {"ok": True}

PROJECT_REMOVAL_LOCK = threading.RLock()


class RemoveProject(BaseModel):
    confirmation: str
    archive: bool = True


@app.post("/api/projects/{name}/remove")
def remove_project(name: str, payload: RemoveProject, request: Request):
    """Stop only this app's PM2 processes and archive project files; never drop DBs."""
    assert_auth(request)
    require_secure_database_admin(request)
    if not payload.archive or payload.confirmation != name:
        raise HTTPException(400, "Confirme digitando exatamente o nome da aplicação")
    with PROJECT_REMOVAL_LOCK:
        entry, path = get_project(name)
        if dj.snapshot(name)["status"] in ("queued", "running"):
            raise HTTPException(409, "Há um deploy em andamento. Finalize-o antes da remoção.")
        # Docker projects may have active containers and their own persistent volumes.
        # Do not guess whether stopping them would affect another application.
        if entry.get("runtime") == "docker":
            raise HTTPException(409, "Remoção de aplicações Docker requer parar e verificar manualmente os containers. Nenhum arquivo foi removido.")
        # Fail closed if the PM2 process list cannot be inspected.
        try:
            pm2_result = subprocess.run(["pm2", "jlist"], env=subprocess_env(),
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
            if pm2_result.returncode != 0 or len(pm2_result.stdout) > 4_000_000:
                raise ValueError("PM2 indisponível")
            raw_processes = json.loads(pm2_result.stdout)
            if not isinstance(raw_processes, list):
                raise ValueError("Resposta PM2 inválida")
            active = [{"name": p.get("name"),
                       "pm2_env": {"pm_cwd": p.get("pm2_env", {}).get("pm_cwd")}}
                      for p in raw_processes if isinstance(p, dict)]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise HTTPException(503, "Não foi possível verificar os processos PM2; remoção cancelada")
        owned = []
        for p in active:
            cwd = p.get("pm2_env", {}).get("pm_cwd")
            if (isinstance(cwd, str) and Path(cwd).resolve().is_relative_to(path)) or p.get("name") == "lm-" + name:
                owned.append(p)
        # Check names against other PM2 workdirs; do not stop ambiguous/shared processes.
        process_names = sorted(set(p["name"] for p in owned if p.get("name")))
        for pname in process_names:
            matches = [p for p in active if p.get("name") == pname]
            if len(matches) != 1:
                raise HTTPException(409, f"Processo {pname} ambíguo; remoção cancelada")
        archive_base = ROOT.parent / "archived-projects"
        archive_base.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive_dir = archive_base / (name + "-" + str(int(time.time())) + "-" + secrets.token_hex(4))
        archive_dir.mkdir(mode=0o700)
        try:
            for pname in process_names:
                command(["pm2", "delete", pname], timeout=35)
            if process_names:
                command(["pm2", "save"], timeout=25)
            # Rename on same filesystem: retained for recovery, never rm -rf.
            os.replace(path, archive_dir / "project")
            secret_file = ROOT.parent / "env" / (name + ".json")
            if secret_file.is_file() and not secret_file.is_symlink():
                os.replace(secret_file, archive_dir / "variables.json")
            data = records()
            data.pop(name, None)
            save(data)
        except Exception:
            # No destructive directory removal: retained archive can be recovered.
            raise
        return {"removed": True, "name": name,
                "archived_at": str(archive_dir), "stopped_processes": process_names,
                "database_preserved": True}


@app.post("/api/projects/{name}/pull")
def update_project(name: str, request: Request):
    assert_auth(request)
    entry, path = get_project(name)
    return dj.start_job(name, ROOT, entry, "update")

@app.get("/api/pm2")
def pm2_list(request: Request):
    assert_auth(request)
    processes = pm2_process_snapshot()
    try:
        listeners = psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error):
        listeners = []
    for process in processes:
        entries = project_listeners([process], [], listeners)
        process["ports"] = [
            {"port": item["port"], "address": item["address"]}
            for item in entries if item["listening"]
        ]
    return processes

@app.post("/api/pm2/processes/{process_name}/{action}")
def manage_pm2_process(process_name: str, action: str, request: Request):
    assert_auth(request)
    if action not in ("start", "stop", "restart", "logs"):
        raise HTTPException(400, "Ação PM2 inválida")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}", process_name):
        raise HTTPException(400, "Nome do processo inválido")
    matches = [p for p in pm2_process_snapshot() if p.get("name") == process_name]
    if len(matches) != 1:
        raise HTTPException(409, "Processo ausente ou nome duplicado no PM2")
    cwd = matches[0].get("pm2_env", {}).get("pm_cwd")
    owner = None
    if isinstance(cwd, str):
        for name in records():
            if Path(cwd).resolve().is_relative_to((ROOT / name).resolve()):
                owner = name
                break
    if not owner:
        raise HTTPException(403, "Operações permitidas somente para processos de aplicações cadastradas")
    if action == "logs":
        output = command(["pm2", "logs", process_name, "--lines", "80", "--nostream", "--raw"], timeout=20)
        values = dj.read_env(ROOT, owner)
        return {"output": dj.mask(output, values)}
    return {"output": command(["pm2", action, process_name], timeout=40)}


@app.post("/api/projects/{name}/pm2/start")
def pm2_start(name: str, payload: Start, request: Request):
    assert_auth(request)
    _, path = get_project(name)
    script = (path / payload.script).resolve()
    if not script.is_relative_to(path) or not script.is_file():
        raise HTTPException(400, "Script não encontrado dentro do projeto")
    if script.suffix not in (".js", ".cjs", ".mjs", ".py"):
        raise HTTPException(400, "Use scripts .js, .cjs, .mjs ou .py")
    args = ["pm2", "start", str(script), "--name", f"lm-{name}", "--cwd", str(path)]
    if script.suffix == ".py":
        args += ["--interpreter", "python3"]
    return {"output": command(args, timeout=40)}

@app.post("/api/projects/{name}/pm2/{action}")
def pm2_action(name: str, action: str, request: Request):
    assert_auth(request)
    get_project(name)
    if action not in ("stop", "restart", "delete"):
        raise HTTPException(400, "Ação inválida")
    return {"output": command(["pm2", action, f"lm-{name}"], timeout=40)}

@app.get("/api/projects/{name}/pm2/logs")
def pm2_logs(name: str, request: Request):
    assert_auth(request)
    get_project(name)
    return {"output": command(["pm2", "logs", f"lm-{name}", "--lines", "80", "--nostream"], timeout=20)}

@app.get("/api/docker")
def docker_list(request: Request):
    assert_auth(request)
    return {"output": command(["docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], timeout=20)}

@app.get("/api/containers")
def containers_inventory(request: Request):
    assert_auth(request)
    return {"containers": cm.docker_list(), "services": cm.service_catalog()}


@app.post("/api/containers/services/{service}/{action}")
def managed_container_action(service: str, action: str, payload: cm.ServiceAction, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return cm.manage(service, action, payload.confirmation)


@app.post("/api/containers/services/{service}/logs")
def managed_container_logs(service: str, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return JSONResponse(cm.managed_logs(service), headers={"Cache-Control": "no-store"})


@app.post("/api/containers/services/redis/bind")
def managed_redis_bind(payload: cm.RedisBind, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return cm.bind_redis(payload.project, records())


@app.post("/api/projects/{name}/docker/{action}")
def docker_action(name: str, action: str, request: Request):
    assert_auth(request)
    _, path = get_project(name)
    if not any((path / filename).is_file() for filename in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")):
        raise HTTPException(400, "Compose não encontrado no projeto")
    if action == "up":
        args = ["docker", "compose", "-p", f"lm-{name}", "up", "-d", "--build"]
    elif action == "down":
        args = ["docker", "compose", "-p", f"lm-{name}", "down"]
    elif action == "logs":
        args = ["docker", "compose", "-p", f"lm-{name}", "logs", "--tail", "80", "--no-color"]
    else:
        raise HTTPException(400, "Ação inválida")
    return {"output": command(args, cwd=path, timeout=300)}

def terminal_allowed(ws: WebSocket):
    """Terminal is high privilege. Explicit opt-in and same-origin only."""
    origin = ws.headers.get("origin", "")
    parsed = urlparse(origin)
    host = ws.headers.get("host", "")
    if not TERMINAL_ENABLED or not authorized(ws.cookies.get("lm_session")):
        return False
    if not origin or parsed.netloc != host or parsed.scheme not in ("http", "https"):
        return False
    # Do not allow a web shell over an insecure public HTTP connection.
    # HTTP is accepted only on loopback for an SSH tunnel to the server.
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    return True


async def project_terminal_session(ws: WebSocket, cwd: Path):
    if not terminal_allowed(ws):
        await ws.close(code=1008)
        return
    actor = session_actor(ws.cookies.get("lm_session"))
    if not actor:
        await ws.close(code=1008)
        return
    identities.log(actor["id"], actor["username"], "terminal.connected",
                   "project:" + cwd.name, 101, ws.client.host if ws.client else "")
    await ws.accept()
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-i"], stdin=slave, stdout=slave,
        stderr=slave, cwd=cwd, start_new_session=True,
        env={**subprocess_env(), "TERM": "xterm-256color", "HISTFILE": "/dev/null"},
    )
    os.close(slave)
    async def pump():
        while True:
            data = await asyncio.to_thread(os.read, master, 4096)
            if not data:
                break
            await ws.send_text(data.decode(errors="replace"))
    task = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive_text()
            if len(msg) > 8192:
                await ws.close(code=1009)
                break
            await asyncio.to_thread(os.write, master, msg.encode())
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        identities.log(actor["id"], actor["username"], "terminal.disconnected",
                       "project:" + cwd.name, 200, ws.client.host if ws.client else "")
        task.cancel()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.close(master)
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=3)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@app.websocket("/ws/projects/{name}/terminal")
async def application_terminal(ws: WebSocket, name: str):
    if not NAME.fullmatch(name):
        await ws.close(code=1008)
        return
    try:
        _, cwd = get_project(name)
    except HTTPException:
        await ws.close(code=1008)
        return
    await project_terminal_session(ws, cwd)


@app.websocket("/ws/terminal")
async def terminal(ws: WebSocket):
    await project_terminal_session(ws, ROOT)


# --- Application deployment management (v2) ---
import shlex
import socket
from typing import Literal

class AppConfig(BaseModel):
    runtime: Literal["node", "python", "docker"] = "node"
    port: int = Field(default=3000, ge=1024, le=65535)
    install_command: str = Field(default="", max_length=400)
    build_command: str = Field(default="", max_length=400)
    start_command: str = Field(default="", max_length=400)
    prepare_command: str = Field(default="", max_length=2000)
    process_mode: Literal["command", "ecosystem"] = "command"
    additional_ports: list[int] = Field(default_factory=list, max_length=20)

def config_for(entry):
    return {
        "runtime": entry.get("runtime", "node"),
        "port": entry.get("port", 3000),
        "install_command": entry.get("install_command", ""),
        "build_command": entry.get("build_command", ""),
        "start_command": entry.get("start_command", ""),
        "prepare_command": entry.get("prepare_command", ""),
        "process_mode": entry.get("process_mode", "command"),
        "additional_ports": entry.get("additional_ports", []),
    }

def run_step(step: str, path: Path, timeout: int = 240):
    if not step.strip():
        return "Etapa ignorada (sem comando configurado)"
    try:
        args = shlex.split(step)
    except ValueError:
        raise HTTPException(400, "Comando malformado")
    if not args or len(args) > 30:
        raise HTTPException(400, "Comando inválido")
    # Only command/arguments, no shell expansion, pipelines or redirection.
    if any(any(ch in a for ch in ("\n", "\r", "\x00")) for a in args):
        raise HTTPException(400, "Caracteres inválidos")
    if args[0] not in ("npm", "pnpm", "yarn", "corepack", "pip", "pip3", "python", "python3", "poetry", "uv"):
        raise HTTPException(400, "Ferramenta não permitida neste comando")
    if args[0] in ("python", "python3") and len(args) > 1 and args[1] not in ("-m",):
        raise HTTPException(400, "Use python3 -m ... para instalar/build")
    return command(args, cwd=path, timeout=timeout)

@app.put("/api/projects/{name}/config")
def configure_project(name: str, payload: AppConfig, request: Request):
    assert_auth(request)
    get_project(name)
    if len(set(payload.additional_ports)) != len(payload.additional_ports):
        raise HTTPException(400, "Portas adicionais duplicadas")
    if payload.port in payload.additional_ports:
        raise HTTPException(400, "A porta principal não deve ser repetida nas portas adicionais")
    if any(port < 1024 or port > 65535 for port in payload.additional_ports):
        raise HTTPException(400, "Porta adicional fora do intervalo permitido")
    data = records()
    data[name].update(payload.model_dump())
    save(data)
    return {"ok": True, "config": config_for(data[name])}

@app.post("/api/projects/{name}/deploy")
def deploy_project(name: str, request: Request):
    assert_auth(request)
    entry, path = get_project(name)
    return dj.start_job(name, ROOT, entry, "deploy")

def start_configured(name, entry, path):
    conf = config_for(entry)
    start = conf["start_command"]
    if not start.strip():
        raise HTTPException(400, "Configure um comando de inicialização")
    try:
        args = shlex.split(start)
    except ValueError:
        raise HTTPException(400, "Comando inicial inválido")
    if not args or len(args) > 30 or any("\n" in a or "\r" in a for a in args):
        raise HTTPException(400, "Comando inicial inválido")
    # PM2 is responsible for process lifecycle, not a shell.
    if conf["runtime"] == "node":
        if args[0] not in ("npm", "pnpm", "yarn", "node"):
            raise HTTPException(400, "Inicialização Node aceita npm, pnpm, yarn ou node")
    elif conf["runtime"] == "python":
        if args[0] not in ("python", "python3", "uv", "uvicorn", "gunicorn"):
            raise HTTPException(400, "Inicialização Python aceita python, uv, uvicorn ou gunicorn")
    else:
        raise HTTPException(400, "Runtime inválido")
    # Stop/recreate configured process to apply new env/port and command.
    subprocess.run(["pm2", "delete", f"lm-{name}"], cwd=path, capture_output=True, timeout=25)
    env = subprocess_env()
    env["PORT"] = str(conf["port"])
    try:
        result = subprocess.run(
            ["pm2", "start", args[0], "--name", f"lm-{name}", "--cwd", str(path), "--", *args[1:]],
            cwd=path, env=env, capture_output=True, text=True, timeout=45
        )
    except FileNotFoundError:
        raise HTTPException(400, "PM2 não encontrado")
    if result.returncode:
        raise HTTPException(400, (result.stdout + result.stderr)[-12000:])
    return (result.stdout + result.stderr)[-12000:]

@app.post("/api/projects/{name}/process/{action}")
def configured_process(name: str, action: str, request: Request):
    assert_auth(request)
    entry, path = get_project(name)
    if config_for(entry)["process_mode"] == "ecosystem" and config_for(entry)["runtime"] != "docker":
        if not (path / "ecosystem.config.cjs").is_file():
            raise HTTPException(400, "ecosystem.config.cjs não encontrado")
        if action in ("start", "restart"):
            return {"output": command(["pm2", "startOrReload", "ecosystem.config.cjs", "--env", "production", "--update-env"], cwd=path, timeout=45)}
        if action == "stop":
            return {"output": command(["pm2", "stop", "ecosystem.config.cjs"], cwd=path, timeout=30)}
        if action == "logs":
            processes = pm2_process_snapshot()
            owned = [p.get("name") for p in processes
                     if p.get("name") and Path(p.get("pm2_env", {}).get("pm_cwd", "/")).resolve().is_relative_to(path)]
            if not owned:
                return {"output": "Nenhum processo deste ecosystem está registrado no PM2"}
            lines = []
            for process_name in owned:
                lines.append(process_name + "\n" + command(["pm2", "logs", process_name, "--lines", "50", "--nostream"], timeout=20))
            return {"output": "\n\n".join(lines)}
    if config_for(entry)["runtime"] == "docker":
        if action in ("start", "restart"):
            return docker_action(name, "up", request)
        if action == "stop":
            return docker_action(name, "down", request)
        if action == "logs":
            return docker_action(name, "logs", request)
    if action == "start":
        return {"output": start_configured(name, entry, path)}
    if action in ("stop", "restart"):
        return pm2_action(name, action, request)
    if action == "logs":
        return pm2_logs(name, request)
    raise HTTPException(400, "Ação desconhecida")

def pm2_process_snapshot():
    """Read complete PM2 JSON without command()'s 12 KB log truncation.

    Only return safe process metadata. PM2's raw jlist includes environment
    variables (and may contain secrets), so never return it to the browser.
    """
    try:
        result = subprocess.run(
            ["pm2", "jlist"], env=subprocess_env(), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0 or len(result.stdout) > 4_000_000:
            return []
        items = json.loads(result.stdout)
        if not isinstance(items, list):
            return []
        return [
            {"name": p.get("name"), "pid": p.get("pid"),
             "pm2_env": {"pm_cwd": p.get("pm2_env", {}).get("pm_cwd"),
                         "status": p.get("pm2_env", {}).get("status", "offline"),
                         "restart_time": p.get("pm2_env", {}).get("restart_time", 0),
                         "pm_uptime": p.get("pm2_env", {}).get("pm_uptime", 0)},
             "monit": {"cpu": p.get("monit", {}).get("cpu", 0),
                       "memory": p.get("monit", {}).get("memory", 0)}}
            for p in items if isinstance(p, dict)
        ]
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return []


def project_listeners(processes, configured_ports, listeners):
    """Attribute listening TCP sockets to PM2 roots and their child processes.

    Binding to 127.0.0.1 does not imply network availability. A configured
    port without an owning socket is displayed as not detected, never open.
    """
    owned = {}
    for p in processes:
        pid = p.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        process_name = p.get("name") or "processo"
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for item in [proc, *children]:
                owned[item.pid] = process_name
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            owned[pid] = process_name
    by_port = {}
    for conn in listeners:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr or conn.pid not in owned:
            continue
        port = conn.laddr.port
        if not (1 <= port <= 65535):
            continue
        address = str(conn.laddr.ip)
        scope = "local" if address in ("127.0.0.1", "::1") else "network"
        item = {"port": port, "address": address, "scope": scope,
                "process": owned[conn.pid], "listening": True}
        by_port[(port, address)] = item
    for port in configured_ports:
        if not any(p == port for p, _ in by_port):
            by_port[(port, "")] = {"port": port, "address": None,
                                   "scope": "unknown", "process": None,
                                   "listening": False}
    return sorted(by_port.values(), key=lambda item: (item["port"], item["address"] or ""))[:40]


@app.get("/api/projects/status")
def project_status(request: Request):
    assert_auth(request)
    data = records()
    processes = pm2_process_snapshot()
    by_name = {p.get("name"): p for p in processes}
    try:
        listeners = psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error):
        listeners = []
    result = []
    for name, entry in data.items():
        proc = by_name.get(f"lm-{name}")
        conf = config_for(entry)
        status = (proc or {}).get("pm2_env", {}).get("status", "offline")
        matched = [proc] if proc else []
        if conf["process_mode"] == "ecosystem" and conf["runtime"] != "docker":
            project_path = (ROOT / name).resolve()
            matched = [
                p for p in processes
                if isinstance(p.get("pm2_env", {}).get("pm_cwd"), str)
                and Path(p["pm2_env"]["pm_cwd"]).resolve().is_relative_to(project_path)
            ]
            if matched:
                online = sum(p.get("pm2_env", {}).get("status") == "online" for p in matched)
                status = "online" if online == len(matched) else "degraded"
                proc = matched[0]
        if conf["runtime"] == "docker":
            status = "docker"
        requested_ports = [conf["port"], *conf["additional_ports"]]
        if conf["runtime"] == "docker":
            endpoints = [{"port": port, "address": None, "scope": "unknown",
                          "process": None, "listening": False}
                         for port in sorted(set(requested_ports))]
        else:
            endpoints = project_listeners(matched, requested_ports, listeners)
        result.append({
            "name": name, "runtime": conf["runtime"], "port": conf["port"],
            "endpoints": endpoints,
            "status": status, "pid": (proc or {}).get("pid"),
            "restarts": sum(p.get("pm2_env", {}).get("restart_time", 0) or 0 for p in matched),
            "process_count": len(matched),
            "online_count": sum(p.get("pm2_env", {}).get("status") == "online" for p in matched),
        })
    return result

@app.get("/api/ports")
def occupied_ports(request: Request):
    assert_auth(request)
    out = []
    for c in psutil.net_connections(kind="inet"):
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        out.append({"port": c.laddr.port, "address": c.laddr.ip, "pid": c.pid})
    return sorted(out, key=lambda item: (item["port"], item["address"]))[:300]

@app.get("/api/host")
def host_details(request: Request):
    assert_auth(request)
    disk = psutil.disk_usage("/")
    mem = psutil.virtual_memory()
    return {
        "hostname": socket.gethostname(), "cpu_count": psutil.cpu_count(),
        "memory_used": mem.used, "memory_total": mem.total,
        "disk_used": disk.used, "disk_total": disk.total,
        "uptime": int(time.time() - psutil.boot_time()),
        "node_version": safe_version(["node", "--version"]),
        "pm2_version": safe_version(["pm2", "--version"]),
        "docker_version": safe_version(["docker", "--version"]),
    }

def safe_version(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "Não instalado"


# --- Deploy jobs, private project environment and optional Git polling ---
@app.get("/api/projects/{name}/env")
def project_env_keys(name: str, request: Request):
    assert_auth(request)
    get_project(name)
    return {"keys": dj.env_keys(ROOT, name)}

@app.get("/api/projects/{name}/env/example")
def project_env_example(name: str, request: Request):
    assert_auth(request)
    _, path = get_project(name)
    return dj.example_env_keys(path, dj.read_env(ROOT, name))

@app.post("/api/projects/{name}/env/document/open")
def project_env_document(name: str, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    get_project(name)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        dj.read_env_document(ROOT, name),
        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache",
                 "X-Content-Type-Options": "nosniff"},
    )


@app.put("/api/projects/{name}/env/document")
def project_env_document_update(name: str, payload: dj.EnvDocument, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    get_project(name)
    return dj.write_env_document(ROOT, name, payload)


@app.post("/api/projects/{name}/env/sync")
def project_env_sync(name: str, request: Request):
    assert_auth(request)
    get_project(name)
    require_secure_database_admin(request)
    return dj.sync_project_dotenv(ROOT, name)


@app.put("/api/projects/{name}/env")
def project_env_update(name: str, payload: dj.EnvChange, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    get_project(name)
    return {"keys": dj.save_env(ROOT, name, payload)}

@app.get("/api/projects/{name}/job")
def project_job(name: str, request: Request):
    assert_auth(request)
    get_project(name)
    return dj.snapshot(name)

@app.post("/api/projects/{name}/update-deploy")
def project_update_and_deploy(name: str, request: Request):
    assert_auth(request)
    entry, _ = get_project(name)
    return dj.start_job(name, ROOT, entry, "update_deploy")

@app.put("/api/projects/{name}/auto-deploy")
def project_auto_deploy(name: str, payload: dj.AutoDeploy, request: Request):
    assert_auth(request)
    get_project(name)
    data = records()
    data[name]["auto_deploy"] = payload.enabled
    save(data)
    return {"enabled": payload.enabled}

def auto_git_monitor():
    # Off by default. Only registered projects with auto_deploy=true are inspected.
    while not AUTO_GIT_STOP.wait(180):
        try:
            items = records()
        except HTTPException:
            continue
        for name, entry in items.items():
            if not entry.get("auto_deploy", False):
                continue
            if dj.snapshot(name)["status"] in ("queued", "running"):
                continue
            try:
                _, path = get_project(name)
                branch = entry["branch"]
                env = ghssh.git_environment(subprocess_env())
                subprocess.run(["git", "fetch", "--quiet", "origin", branch],
                               cwd=path, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=45, check=True)
                local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, env=env, timeout=10).strip()
                remote = subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=path, env=env, timeout=10).strip()
                if local != remote:
                    dj.start_job(name, ROOT, entry, "update_deploy")
            except (OSError, subprocess.SubprocessError, HTTPException):
                # A remote or SSH failure should never interrupt monitoring other projects.
                continue

AUTO_GIT_STOP = Event()
AUTO_GIT_THREAD = None

@app.on_event("startup")
def start_auto_git():
    global AUTO_GIT_THREAD
    AUTO_GIT_STOP.clear()
    AUTO_GIT_THREAD = Thread(target=auto_git_monitor, daemon=True, name="git-auto-deploy")
    AUTO_GIT_THREAD.start()

@app.on_event("shutdown")
def stop_auto_git():
    AUTO_GIT_STOP.set()
    if AUTO_GIT_THREAD:
        AUTO_GIT_THREAD.join(timeout=3)


class SSHRepoTest(BaseModel):
    repo: str = Field(min_length=15, max_length=500)

@app.get("/api/ssh/github")
def github_ssh_status(request: Request):
    assert_auth(request)
    return ghssh.status()

@app.post("/api/ssh/github/generate")
def github_ssh_generate(request: Request):
    assert_auth(request)
    ghssh.generate_key()
    return ghssh.status()

@app.post("/api/ssh/github/test")
def github_ssh_test(payload: SSHRepoTest, request: Request):
    assert_auth(request)
    return ghssh.test_repository(payload.repo)


def _trusted_proxy(peer: str) -> bool:
    """Never trust forwarded headers unless the TCP peer is explicitly allowlisted.

    TRUSTED_PROXY_IPS accepts comma-separated IPs or CIDRs. Only use exact
    Traefik source IPs or a narrowly scoped network, never 0.0.0.0/0.
    """
    configured = os.environ.get("TRUSTED_PROXY_IPS", "")
    try:
        remote = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for part in configured.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError:
            continue
        if network.prefixlen == 0:
            continue
        if remote in network:
            return True
    return False


def _secure_proxy_origin(request: Request) -> bool:
    """Confirm an HTTPS-origin browser request through a trusted proxy peer.

    Do not accept the untrusted X-Forwarded-Proto header by itself.
    Require Origin and the forwarded Host to match the browser-visible Host.
    """
    peer = request.client.host if request.client else ""
    if not _trusted_proxy(peer):
        return False
    if request.headers.get("x-forwarded-proto", "").strip().lower() != "https":
        return False
    origin = urlparse(request.headers.get("origin", ""))
    host = request.headers.get("host", "")
    if not host or origin.scheme != "https" or origin.netloc != host:
        return False
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host and forwarded_host != host:
        return False
    return True


def require_secure_database_admin(request: Request):
    # Accept HTTPS at the app, or HTTPS terminated at explicitly trusted
    # Traefik, or a local SSH tunnel. Never use arbitrary forwarded headers.
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1") and request.headers.get("host", "").split(":")[0] in ("localhost", "127.0.0.1", "[::1]"):
        return
    if request.url.scheme == "https" and request.headers.get("origin", "").startswith("https://"):
        return
    if _secure_proxy_origin(request):
        return
    raise HTTPException(403, "Operações de bancos exigem HTTPS através de proxy confiável ou túnel SSH local")


# --- Managed PostgreSQL/MySQL (single host) ---
@app.get("/api/databases/services")
def managed_services(request: Request):
    assert_auth(request)
    return dbm.services()

@app.post("/api/databases/services/{engine}/start")
def managed_service_start(engine: str, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return dbm.start(engine)

@app.get("/api/databases")
def managed_databases(request: Request):
    assert_auth(request)
    return dbm.list_db()

@app.post("/api/databases")
def managed_database_create(payload: dbm.NewDatabase, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return dbm.create(payload, records())

@app.post("/api/databases/{engine}/{name}/users")
def managed_db_user_create(engine: str, name: str, payload: dbm.NewUser, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return dbm.add_user(engine, name, payload)

@app.post("/api/databases/{engine}/{name}/bind")
def managed_db_bind(engine: str, name: str, payload: dbm.BindUser, request: Request):
    assert_auth(request)
    require_secure_database_admin(request)
    return dbm.bind(engine, name, payload, records())
