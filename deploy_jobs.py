"""Project-scoped deploy jobs and .env management. Single Uvicorn worker per installation."""
import json
import hashlib
import os
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field
import github_ssh as ghssh

ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
RESERVED = {"ADMIN_PASSWORD", "SESSION_SECRET", "PM2_HOME", "HOME", "PATH", "PORT"}
ALLOWED_TOOLS = {"docker", "npm", "pnpm", "yarn", "corepack", "node", "python", "python3", "pip", "pip3", "uv", "uvicorn", "gunicorn", "poetry"}
JOBS = {}
JOBS_LOCK = threading.Lock()
LOG_LIMIT = 120_000
ANSI_ESCAPE = re.compile(r"(?:\\x1b|\\ufffd)\\[[0-9;?]*[ -/]*[@-~]")


class EnvChange(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)


class AutoDeploy(BaseModel):
    enabled: bool = False


def env_path(projects_root: Path, name: str) -> Path:
    folder = projects_root.parent / "env"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder / (name + ".json")


def read_env(projects_root: Path, name: str) -> dict[str, str]:
    f = env_path(projects_root, name)
    if not f.exists():
        return {}
    try:
        value = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise ValueError("Invalid env file")
        return value
    except (OSError, ValueError):
        raise HTTPException(500, "Arquivo de variáveis do projeto inválido")


def env_keys(projects_root: Path, name: str) -> list[str]:
    return sorted(read_env(projects_root, name))


def example_env_keys(project_path: Path, stored: dict[str, str]) -> dict:
    """Read a tracked .env.example as suggestions only; never execute or save sample values."""
    source = project_path / ".env.example"
    if not source.is_file() or source.is_symlink():
        return {"source": None, "variables": [], "missing": 0}
    try:
        if source.stat().st_size > 131072:
            raise HTTPException(400, ".env.example é grande demais")
        raw = source.read_text(encoding="utf-8")
    except UnicodeError:
        raise HTTPException(400, ".env.example não está em UTF-8")
    names = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, _ = line.partition("=")
        key = key.strip()
        if not sep or key in seen or not ENV_KEY.fullmatch(key) or key in RESERVED:
            continue
        seen.add(key)
        names.append({"key": key, "configured": key in stored and bool(stored[key])})
        if len(names) >= 100:
            break
    return {"source": ".env.example", "variables": names,
            "missing": sum(not row["configured"] for row in names)}


ENV_HEADER = "# Managed by Linux Manager. Do not edit directly; use the panel."
ENV_WRITE_LOCK = threading.RLock()


def _dotenv_quote(value: str) -> str:
    """Encode one value for Node/Python dotenv parsers without shell expansion."""
    if any(char in value for char in ("\x00", "\n", "\r")):
        raise HTTPException(400, "Variáveis com quebras de linha não podem ser sincronizadas no .env")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sync_project_dotenv(projects_root: Path, name: str, values: dict[str, str] | None = None) -> dict:
    """Materialize private managed .env in project root; never modify an unmanaged .env."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", name):
        raise HTTPException(400, "Nome de projeto inválido")
    project = projects_root / name
    if not project.is_dir() or project.is_symlink():
        raise HTTPException(404, "Diretório do projeto não encontrado")
    target = project / ".env"
    with ENV_WRITE_LOCK:
        if target.is_symlink():
            raise HTTPException(409, "O arquivo .env é um link simbólico; sincronização recusada")
        if target.exists():
            if not target.is_file():
                raise HTTPException(409, ".env existente não é arquivo regular")
            try:
                with target.open("r", encoding="utf-8") as file:
                    first_line = file.readline().rstrip("\r\n")
                if first_line != ENV_HEADER:
                    raise HTTPException(409, ".env já existe e não é gerenciado pelo Linux Manager; não será sobrescrito")
            except UnicodeError:
                raise HTTPException(409, ".env existente possui codificação incompatível")
        stored = read_env(projects_root, name) if values is None else values
        if any(not ENV_KEY.fullmatch(k) or k in RESERVED for k in stored):
            raise HTTPException(400, "Nome de variável inválido no cofre")
        content = ENV_HEADER + "\n" + "".join(
            key + "=" + _dotenv_quote(value) + "\n"
            for key, value in sorted(stored.items())
        )
        temp = project / ".env.linux-manager.tmp"
        if temp.is_symlink():
            raise HTTPException(409, "Arquivo temporário de .env inseguro")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return {"synced": True, "file": ".env", "keys": sorted(stored)}


class EnvDocument(BaseModel):
    content: str = Field(max_length=131072)
    revision: str = Field(min_length=64, max_length=64)


def env_revision(values: dict[str, str]) -> str:
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_env_document(projects_root: Path, name: str) -> dict:
    with ENV_WRITE_LOCK:
        values = read_env(projects_root, name)
        text = ENV_HEADER + "\n" + "".join(
            key + "=" + _dotenv_quote(value) + "\n"
            for key, value in sorted(values.items())
        )
        return {"content": text, "revision": env_revision(values), "keys": len(values)}


def parse_env_document(content: str) -> dict[str, str]:
    """Strict single-line dotenv parser: no expansion, no shell, no interpolation."""
    if len(content.encode("utf-8")) > 131072 or "\x00" in content:
        raise HTTPException(400, "Documento .env inválido ou muito grande")
    parsed = {}
    for index, source in enumerate(content.splitlines(), 1):
        line = source.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not ENV_KEY.fullmatch(key) or key in RESERVED:
            raise HTTPException(400, f"Linha {index}: nome de variável inválido")
        if key in parsed:
            raise HTTPException(400, f"Linha {index}: variável duplicada ({key})")
        value = value.strip()
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                raise HTTPException(400, f"Linha {index}: valor com aspas duplas inválido")
            if not isinstance(decoded, str):
                raise HTTPException(400, f"Linha {index}: valor deve ser texto")
            value = decoded
        elif value.startswith("'"):
            if not value.endswith("'") or len(value) < 2:
                raise HTTPException(400, f"Linha {index}: aspas simples não fechadas")
            value = value[1:-1]
        if len(value) > 8192 or "\n" in value or "\r" in value:
            raise HTTPException(400, f"Linha {index}: valor inválido ou muito grande")
        parsed[key] = value
        if len(parsed) > 100:
            raise HTTPException(400, "Limite de 100 variáveis por projeto")
    return parsed


def write_env_document(projects_root: Path, name: str, payload: EnvDocument) -> dict:
    with ENV_WRITE_LOCK:
        before = read_env(projects_root, name)
        if env_revision(before) != payload.revision:
            raise HTTPException(409, "Variáveis alteradas em outra sessão. Recarregue o editor antes de salvar.")
        after = parse_env_document(payload.content)
        result = save_env(projects_root, name, EnvChange(
            values=after,
            remove=[key for key in before if key not in after],
        ))
        return {"keys": result, "revision": env_revision(after), "saved": True}


def save_env(projects_root: Path, name: str, change: EnvChange):
    with ENV_WRITE_LOCK:
        return _save_env_locked(projects_root, name, change)


def _save_env_locked(projects_root: Path, name: str, change: EnvChange):
    old = read_env(projects_root, name)
    for k, v in change.values.items():
        if not ENV_KEY.fullmatch(k) or k in RESERVED or len(v) > 8192 or "\x00" in v:
            raise HTTPException(400, "Nome de variável reservado/inválido ou valor muito longo")
    for k in change.remove:
        if not ENV_KEY.fullmatch(k) or k in RESERVED:
            raise HTTPException(400, "Nome de variável inválido")
    old.update(change.values)
    for k in change.remove:
        old.pop(k, None)
    if len(old) > 100:
        raise HTTPException(400, "Limite de 100 variáveis por projeto")
    # Materialize before committing the vault: no partial success on invalid .env.
    sync_project_dotenv(projects_root, name, old)
    f = env_path(projects_root, name)
    tmp = f.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(old, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, f)
    finally:
        tmp.unlink(missing_ok=True)
    return sorted(old)


def environment(projects_root: Path, name: str):
    base = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "SESSION_SECRET")}
    home = str(Path.home())
    base.update({"HOME": home, "PM2_HOME": str(Path(home) / ".pm2")})
    return ghssh.git_environment({**base, **read_env(projects_root, name)})


def safe_args(command):
    try:
        args = shlex.split(command)
    except ValueError:
        raise RuntimeError("Comando inválido")
    if not args or args[0] not in ALLOWED_TOOLS or len(args) > 30:
        raise RuntimeError("Comando não permitido")
    if any("\x00" in a or "\n" in a or "\r" in a for a in args):
        raise RuntimeError("Argumentos inválidos")
    if args[0] == "docker" and args[1:3] != ["compose", "up"]:
        raise RuntimeError("Somente docker compose up é permitido nas etapas de preparo")
    if args[0] == "docker" and args[1:] not in (["compose", "up", "-d", "postgres"], ["compose", "up", "-d"]):
        raise RuntimeError("Comando Docker de preparo não permitido")
    if args[0] in ("python", "python3") and len(args) >= 2 and args[1] == "-c":
        raise RuntimeError("Python -c não permitido")
    return args


def mask(text, secrets):
    # PM2 emits ANSI formatting, which belongs in a TTY, not in HTML logs.
    text = ANSI_ESCAPE.sub("", str(text))
    for value in sorted(set(secrets.values()), key=len, reverse=True):
        if not value or len(value) < 4:
            continue
        if re.fullmatch(r"[A-Za-z0-9_]+", value):
            # Avoid replacing "system" inside "ecosystem".
            text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", "[REDACTED]", text)
        else:
            text = text.replace(value, "[REDACTED]")
    return text


def run_process(args, cwd, env, add_log, timeout=300):
    # Shell is never invoked. Stream line buffered stdout and stderr to a bounded job log.
    add_log("$ " + " ".join(shlex.quote(a) for a in args) + "\n")
    env = {**env, "NO_COLOR": "1", "FORCE_COLOR": "0", "CLICOLOR": "0", "PM2_NO_COLOR": "true"}
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1, start_new_session=True)
    except OSError as exc:
        raise RuntimeError(f"Falha ao iniciar {args[0]}: {exc}") from exc
    started = time.monotonic()
    try:
        # Timeout is enforced while reading by a separate watchdog.
        timer = threading.Timer(timeout, lambda: proc.poll() is None and proc.kill())
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                add_log(line)
            code = proc.wait()
        finally:
            timer.cancel()
        if time.monotonic() - started >= timeout:
            raise RuntimeError("Tempo limite da etapa excedido")
        if code != 0:
            raise RuntimeError(f"Etapa falhou (exit {code})")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def start_job(name, root, entry, action, finish=None):
    """One job at a time per project. Returns a read-only job snapshot."""
    with JOBS_LOCK:
        previous = JOBS.get(name)
        if previous and previous["status"] in ("queued", "running"):
            raise HTTPException(409, "Já existe uma operação em execução para este projeto")
        job = {"status": "queued", "action": action, "started": time.time(),
               "ended": None, "log": "", "error": None}
        JOBS[name] = job

    def worker():
        secrets = {}
        project_path = root / name

        def append(line):
            line = mask(str(line), secrets)
            with JOBS_LOCK:
                job["log"] = (job["log"] + line)[-LOG_LIMIT:]

        with JOBS_LOCK:
            job["status"] = "running"
        try:
            secrets = read_env(root, name)
            if action in ("deploy", "update_deploy"):
                sync_project_dotenv(root, name, secrets)
            env = environment(root, name)
            env["PORT"] = str(entry.get("port", 3000))
            if action in ("update", "update_deploy"):
                branch = entry.get("branch", "main")
                run_process(["git", "-c", "core.hooksPath=/dev/null", "pull", "--ff-only", "origin", branch],
                            project_path, {**env, "GIT_TERMINAL_PROMPT": "0"}, append, 120)
            if action in ("deploy", "update_deploy"):
                if entry.get("runtime", "node") == "docker":
                    run_process(["docker", "compose", "-p", "lm-" + name, "up", "-d", "--build"],
                                project_path, env, append, 600)
                else:
                    for key in ("install_command", "prepare_command", "build_command"):
                        value = entry.get(key, "").strip()
                        for line in value.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#"):
                                run_process(safe_args(line), project_path, env, append, 600)
                    if entry.get("process_mode", "command") == "ecosystem":
                        ecosystem = project_path / "ecosystem.config.cjs"
                        if not ecosystem.is_file():
                            raise RuntimeError("ecosystem.config.cjs não encontrado na raiz do repositório")
                        run_process(["pm2", "startOrReload", "ecosystem.config.cjs",
                                     "--env", "production", "--update-env"],
                                    project_path, env, append, 45)
                        run_process(["pm2", "save"], project_path, env, append, 20)
                        append("\nProcessos do ecosystem iniciados.\n")
                        with JOBS_LOCK:
                            job["status"] = "success"
                        return
                    starter = entry.get("start_command", "").strip()
                    if not starter:
                        raise RuntimeError("Configure o comando de inicialização antes do deploy")
                    args = safe_args(starter)
                    runtime = entry.get("runtime", "node")
                    allowed = {"node": {"node", "npm", "pnpm", "yarn"},
                               "python": {"python", "python3", "uv", "uvicorn", "gunicorn"}}
                    if args[0] not in allowed.get(runtime, set()):
                        raise RuntimeError("Comando de inicialização incompatível com o runtime")
                    subprocess.run(["pm2", "delete", "lm-" + name], cwd=project_path, env=env,
                                   capture_output=True, timeout=20)
                    run_process(["pm2", "start", args[0], "--name", "lm-" + name,
                                 "--cwd", str(project_path), "--", *args[1:]],
                                project_path, env, append, 45)
                    run_process(["pm2", "save"], project_path, env, append, 20)
            append("\nOperação concluída.\n")
            with JOBS_LOCK:
                job["status"] = "success"
        except Exception as exc:
            append("\nERRO: " + str(exc) + "\n")
            with JOBS_LOCK:
                job["status"] = "failed"
                job["error"] = mask(str(exc), secrets)
        finally:
            with JOBS_LOCK:
                job["ended"] = time.time()
            if finish:
                finish(name, job["status"])

    threading.Thread(target=worker, daemon=True, name="deploy-" + name).start()
    return snapshot(name)


def snapshot(name):
    with JOBS_LOCK:
        job = JOBS.get(name)
        return {"status": job["status"], "action": job["action"], "started": job["started"],
                "ended": job["ended"], "log": job["log"], "error": job["error"]} if job else {
                    "status": "idle", "action": None, "log": "", "error": None, "started": None, "ended": None
                }
