"""Safe, fixed-catalog service orchestration for Linux Manager.

Managed services use the same Compose project and volumes as database_manager.
Unmanaged containers are *read-only* in the UI. No arbitrary image execution.
"""
import json
import re
import subprocess
from urllib.parse import quote
from fastapi import HTTPException
from pydantic import BaseModel
import database_manager as dbm
import deploy_jobs as dj

CATALOG = {
    "postgres": {"name": "PostgreSQL", "image": "postgres:17-alpine", "port": 5432, "type": "Database"},
    "mysql": {"name": "MySQL", "image": "mysql:8.4", "port": 3306, "type": "Database"},
    "redis": {"name": "Redis", "image": "redis:7.4-alpine", "port": 6379, "type": "Cache"},
}
MANAGED_COMPOSE = "linux-manager-databases"
ANSI = re.compile(r"(?:\x1b|\ufffd)\[[0-9;?]*[ -/]*[@-~]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ServiceAction(BaseModel):
    confirmation: str = ""


class RedisBind(BaseModel):
    project: str


def clean_output(value: str) -> str:
    """Remove terminal escapes before displaying text (including malformed UTF-8)."""
    value = ANSI.sub("", value)
    return CONTROL.sub("", value).replace("\r", "\n")


def docker_list():
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=18)
        if proc.returncode:
            raise RuntimeError("Docker indisponível")
        records = []
        for line in proc.stdout.splitlines()[:500]:
            if not line.strip():
                continue
            data = json.loads(line)
            labels = data.get("Labels", "")
            # docker ps --format does not expose untrusted ENV secrets.
            managed = (f"com.docker.compose.project={MANAGED_COMPOSE}" in labels)
            records.append({
                "id": data.get("ID", "")[:12],
                "name": data.get("Names", ""),
                "image": data.get("Image", ""),
                "status": data.get("Status", ""),
                "state": data.get("State", ""),
                "ports": data.get("Ports", ""),
                "managed": managed,
            })
        return records
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError):
        raise HTTPException(503, "Não foi possível consultar os containers Docker")


def service_catalog():
    records = docker_list()
    # Managed records can be identified from names and labels. The name on
    # its own is not sufficient for privileged actions.
    known = {}
    for c in records:
        if not c["managed"]:
            continue
        for key in CATALOG:
            if c["name"] == MANAGED_COMPOSE + "-" + key + "-1":
                known[key] = c
    return [
        {**data, "id": key, "container": known.get(key),
         "running": bool(known.get(key, {}).get("state") == "running"),
         "host": "127.0.0.1"}
        for key, data in CATALOG.items()
    ]


def _check(service: str):
    if service not in CATALOG:
        raise HTTPException(400, "Serviço não permitido neste catálogo")
    return service


def manage(service: str, action: str, confirmation: str = ""):
    _check(service)
    if action == "start":
        # dbm.start checks port ownership and proves database credentials.
        if service in ("postgres", "mysql"):
            return dbm.start(service)
        with dbm.LOCK:
            if dbm._occupied(6379) and not dbm.running("redis"):
                raise HTTPException(409, "Porta 6379 ocupada por outro serviço; nenhuma alteração aplicada")
            dbm.compose("up", "-d", "redis", timeout=220)
        return {"service": service, "action": "start", "message": "Serviço Redis iniciado"}
    if action == "stop":
        if confirmation != service:
            raise HTTPException(400, "Confirme o nome exato do serviço para interrompê-lo")
        # Preserve databases actively registered in the Linux Manager catalog.
        if service in ("postgres", "mysql"):
            if any(item["engine"] == service for item in dbm.list_db()):
                raise HTTPException(409, "Há bancos cadastrados neste serviço. Parada bloqueada para proteger as aplicações.")
        with dbm.LOCK:
            dbm.compose("stop", service, timeout=70)
        return {"service": service, "action": "stop", "message": "Serviço interrompido. Volume preservado."}
    raise HTTPException(400, "Ação inválida")


def managed_logs(service: str):
    _check(service)
    # Use named service only: no arbitrary container or command selector.
    with dbm.LOCK:
        output = dbm.compose("logs", "--tail", "100", "--no-color", service, timeout=25)
    # Raw logs can include secrets supplied by the app. Redact service secrets.
    secrets = dbm.root_secrets()
    clean = clean_output(output)
    for value in secrets.values():
        if len(value) >= 8:
            clean = clean.replace(value, "[REDACTED]")
    return {"service": service, "logs": clean[-15000:]}


def bind_redis(project: str, projects: dict):
    if project not in projects:
        raise HTTPException(404, "Aplicação não encontrada")
    if projects[project].get("runtime") == "docker":
        raise HTTPException(400, "Vínculo Redis automático exige aplicação PM2 no host")
    if not dbm.running("redis"):
        raise HTTPException(409, "Redis ainda não foi iniciado")
    passwd = dbm.root_secrets()["REDIS_PASSWORD"]
    url = "redis://:" + quote(passwd, safe="") + "@127.0.0.1:6379/0"
    root = dbm.ROOT / "projects"
    dj.save_env(root, project, dj.EnvChange(values={"REDIS_URL": url}))
    return {"project": project, "variable": "REDIS_URL", "message": "REDIS_URL configurada. Faça novo deploy."}
