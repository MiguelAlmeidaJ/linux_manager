import asyncio
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

def command(args, cwd=None, timeout=60):
    try:
        result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout, env={**{k:v for k,v in os.environ.items() if k not in ("ADMIN_PASSWORD", "SESSION_SECRET")}, "GIT_TERMINAL_PROMPT": "0"})
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
        "cpu": psutil.cpu_percent(interval=0.1), "memory": mem.percent,
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
    command(["git", "clone", "--depth", "1", "--branch", payload.branch, "--", payload.repo, str(path)], timeout=180)
    data[payload.name] = {"repo": payload.repo, "branch": payload.branch}
    save(data)
    return {"ok": True}

@app.post("/api/projects/{name}/pull")
def update_project(name: str, request: Request):
    assert_auth(request)
    entry, path = get_project(name)
    return {"output": command(["git", "-c", "core.hooksPath=/dev/null", "pull", "--ff-only", "origin", entry["branch"]], cwd=path, timeout=120)}

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
    if not (path / "compose.yaml").is_file() and not (path / "docker-compose.yml").is_file() and not (path / "docker-compose.yaml").is_file():
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

@app.websocket("/ws/terminal")
async def terminal(ws: WebSocket):
    host = ws.headers.get("host", "")
    origin = ws.headers.get("origin", "")
    parsed = urlparse(origin)
    if not TERMINAL_ENABLED or not authorized(ws.cookies.get("lm_session")) or not origin or parsed.netloc != host or parsed.scheme not in ("http", "https"):
        await ws.close(code=1008)
        return
    await ws.accept()
    master, slave = pty.openpty()
    process = subprocess.Popen(["/bin/bash", "-i"], stdin=slave, stdout=slave, stderr=slave,
                               cwd=ROOT, start_new_session=True, env={**{k:v for k,v in os.environ.items() if k not in ("ADMIN_PASSWORD", "SESSION_SECRET")}, "TERM": "xterm-256color"})
    os.close(slave)
    loop = asyncio.get_running_loop()
    async def output():
        while True:
            data = await asyncio.to_thread(os.read, master, 4096)
            if not data:
                return
            await ws.send_text(data.decode(errors="replace"))
    task = asyncio.create_task(output())
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
