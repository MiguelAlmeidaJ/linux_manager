import asyncio
from collections import deque
from threading import Event, Lock, Thread
import hmac
import json
import os
import pty
import re
import secrets
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
import deploy_jobs as dj
import github_ssh as ghssh

ROOT = Path(os.environ.get("PROJECTS_DIR", "/srv/linux-manager/projects")).expanduser().resolve()
ROOT.mkdir(parents=True, exist_ok=True)
DATA_FILE = ROOT.parent / "linux-manager-projects.json"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("SESSION_SECRET", "")
if not PASSWORD or not SECRET or len(SECRET) < 32:
    raise RuntimeError("Configure ADMIN_PASSWORD e SESSION_SECRET (mín. 32 caracteres).")
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


def authorized(token):
    try:
        return serializer.loads(token or "", max_age=12 * 3600) == "admin"
    except BadSignature:
        return False

def assert_auth(request: Request):
    if not authorized(request.cookies.get("lm_session")):
        raise HTTPException(401, "Não autenticado")
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        parsed = urlparse(origin)
        # Only same-origin browser requests may mutate the panel
        if parsed.netloc != host or parsed.scheme not in ("http", "https"):
            raise HTTPException(403, "Origem inválida")

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
    password: str

class Project(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    repo: str = Field(min_length=5, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=120)

class Start(BaseModel):
    script: str = Field(default="index.js", min_length=1, max_length=240)

@app.post("/api/login")
def login(payload: Login):
    if not hmac.compare_digest(payload.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "Senha incorreta")
    response = JSONResponse({"ok": True})
    response.set_cookie("lm_session", serializer.dumps("admin"), httponly=True,
                        samesite="strict", secure=SECURE_COOKIE, max_age=12 * 3600, path="/")
    return response

@app.post("/api/logout")
def logout(request: Request):
    assert_auth(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie("lm_session", path="/")
    return response

@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")

@app.get("/api/me")
def me(request: Request):
    assert_auth(request)
    return {"ok": True, "terminal": TERMINAL_ENABLED}

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

@app.post("/api/projects/{name}/pull")
def update_project(name: str, request: Request):
    assert_auth(request)
    entry, path = get_project(name)
    return dj.start_job(name, ROOT, entry, "update")

@app.get("/api/pm2")
def pm2_list(request: Request):
    assert_auth(request)
    output = command(["pm2", "jlist"], timeout=20)
    return json.loads(output)

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

def config_for(entry):
    return {
        "runtime": entry.get("runtime", "node"),
        "port": entry.get("port", 3000),
        "install_command": entry.get("install_command", ""),
        "build_command": entry.get("build_command", ""),
        "start_command": entry.get("start_command", ""),
        "prepare_command": entry.get("prepare_command", ""),
        "process_mode": entry.get("process_mode", "command"),
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
            processes = json.loads(command(["pm2", "jlist"], timeout=20))
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

@app.get("/api/projects/status")
def project_status(request: Request):
    assert_auth(request)
    data = records()
    try:
        processes = json.loads(command(["pm2", "jlist"], timeout=15))
    except (HTTPException, ValueError):
        processes = []
    by_name = {p.get("name"): p for p in processes}
    result = []
    for name, entry in data.items():
        proc = by_name.get(f"lm-{name}")
        conf = config_for(entry)
        status = (proc or {}).get("pm2_env", {}).get("status", "offline")
        if conf["runtime"] == "docker":
            status = "docker"
        result.append({
            "name": name, "runtime": conf["runtime"], "port": conf["port"],
            "status": status, "pid": (proc or {}).get("pid"),
            "restarts": (proc or {}).get("pm2_env", {}).get("restart_time", 0),
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

@app.put("/api/projects/{name}/env")
def project_env_update(name: str, payload: dj.EnvChange, request: Request):
    assert_auth(request)
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
