"""Managed databases for a single trusted Linux Manager host.

PostgreSQL and MySQL run via Docker Compose bound to 127.0.0.1. Credentials
are stored in owner-only files; never returned by GET endpoints.
"""
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql
import pymysql
from fastapi import HTTPException
from pydantic import BaseModel, Field
import deploy_jobs

ROOT = Path(os.getenv("PROJECTS_DIR", "/srv/linux-manager/projects")).resolve().parent
ROOT.mkdir(parents=True, exist_ok=True)
STATE = ROOT / "managed-databases.json"
SECRETS = ROOT / "database-services.env"
COMPOSE = Path(__file__).parent / "deploy" / "databases.compose.yml"
LOCK = threading.RLock()
ENGINES = {"postgres": 5432, "mysql": 3306}
IDENT = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
RESERVED = {"postgres", "root", "mysql", "public", "template0", "template1", "linuxmanager_admin"}


class NewDatabase(BaseModel):
    engine: str
    name: str
    username: str
    password: str = Field(min_length=12, max_length=128)
    project: str | None = None


class NewUser(BaseModel):
    username: str
    password: str = Field(min_length=12, max_length=128)
    permission: str = "read_only"


class BindUser(BaseModel):
    username: str
    project: str


def valid(value):
    if not IDENT.fullmatch(value) or value in RESERVED:
        raise HTTPException(400, "Nome inválido: letras minúsculas, números e _ (2–48 caracteres)")
    return value


def engine_ok(engine):
    if engine not in ENGINES:
        raise HTTPException(400, "Banco deve ser postgres ou mysql")
    return engine


def private_write(path, value):
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def root_secrets():
    if not SECRETS.exists():
        private_write(SECRETS,
                      "POSTGRES_ROOT_PASSWORD=" + secrets.token_urlsafe(40) + "\n" +
                      "MYSQL_ROOT_PASSWORD=" + secrets.token_urlsafe(40) + "\n")
    if SECRETS.is_symlink():
        raise HTTPException(500, "Arquivo de segredos inseguro")
    fields = dict(line.split("=", 1) for line in SECRETS.read_text().splitlines() if "=" in line)
    if any(len(fields.get(key, "")) < 24 for key in ("POSTGRES_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD")):
        raise HTTPException(500, "Segredos dos serviços inválidos")
    return fields


def catalog():
    if not STATE.exists():
        return {"databases": []}
    try:
        value = json.loads(STATE.read_text())
        assert isinstance(value["databases"], list)
        return value
    except (ValueError, KeyError, AssertionError, OSError):
        raise HTTPException(500, "Cadastro de bancos inválido")


def write_catalog(data):
    private_write(STATE, json.dumps(data, indent=2))


def compose(*args, timeout=150):
    root_secrets()
    try:
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(SECRETS), "-f", str(COMPOSE),
             "-p", "linux-manager-databases", *args],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout,
            env={k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "SESSION_SECRET")},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(503, "Docker indisponível ou operação excedeu o limite")
    if result.returncode:
        raise HTTPException(502, "Falha no serviço Docker. Confira os logs locais sem divulgar senhas.")
    return result.stdout


def running(engine):
    try:
        return engine in compose("ps", "--services", "--status", "running").splitlines()
    except HTTPException:
        return False


def connection(engine, database=None):
    creds = root_secrets()
    try:
        if engine == "postgres":
            return psycopg.connect(
                host="127.0.0.1", port=5432, user="linuxmanager_admin",
                dbname=database or "postgres", password=creds["POSTGRES_ROOT_PASSWORD"],
                connect_timeout=5, autocommit=True)
        return pymysql.connect(
            host="127.0.0.1", port=3306, user="root",
            password=creds["MYSQL_ROOT_PASSWORD"], database=database,
            connect_timeout=5, read_timeout=15, write_timeout=15, autocommit=True)
    except (psycopg.Error, pymysql.MySQLError, OSError):
        raise HTTPException(503, "Serviço de banco indisponível ou credenciais incompatíveis")


def services():
    out = []
    for engine, port in ENGINES.items():
        with LOCK:
            active = running(engine)
        out.append({"engine": engine, "port": port, "running": active})
    return out


def start(engine):
    engine_ok(engine)
    with LOCK:
        port = ENGINES[engine]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                occupied = True
        except OSError:
            occupied = False
        if occupied and not running(engine):
            raise HTTPException(409, f"Porta {port} ocupada por um serviço externo. Nada foi alterado.")
        compose("up", "-d", engine, timeout=240)
        until = time.monotonic() + 60
        while time.monotonic() < until:
            try:
                connection(engine).close()
                return {"engine": engine, "running": True, "port": port}
            except HTTPException:
                time.sleep(2)
        raise HTTPException(503, "Container iniciado, mas banco ainda não está pronto")


def list_db():
    with LOCK:
        return [
            {"engine": db["engine"], "name": db["name"], "owner": db["owner"],
             "users": [{"username": u["username"], "permission": u["permission"]}
                       for u in db["users"]]}
            for db in catalog()["databases"]
        ]


def find(db_engine, name):
    engine_ok(db_engine)
    valid(name)
    for db in catalog()["databases"]:
        if db["engine"] == db_engine and db["name"] == name:
            return db
    raise HTTPException(404, "Banco não encontrado")


def mysql_grant(cursor, database, user, permission):
    # Identifiers are restricted by IDENT. Quoting protects reserved words.
    q = chr(96)
    scope = q + database + q + ".*"
    role = "'" + user + "'@'%'"
    permissions = {
        "owner": "ALL PRIVILEGES",
        "read_only": "SELECT, SHOW VIEW",
        "read_write": "SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, EXECUTE",
    }
    cursor.execute("GRANT " + permissions[permission] + " ON " + scope + " TO " + role)


def create(payload, projects):
    engine = engine_ok(payload.engine)
    dbname = valid(payload.name)
    username = valid(payload.username)
    if payload.project and payload.project not in projects:
        raise HTTPException(404, "Aplicação não encontrada")
    if payload.project and projects[payload.project].get("runtime") == "docker":
        raise HTTPException(400, "Vinculação automática ainda não suporta apps Docker. Crie o banco sem vincular e configure a rede primeiro.")
    with LOCK:
        data = catalog()
        if any(x["engine"] == engine and (x["name"] == dbname or
               any(u["username"] == username for u in x["users"])) for x in data["databases"]):
            raise HTTPException(409, "Banco ou usuário já cadastrado")
        if not running(engine):
            raise HTTPException(409, "Inicie primeiro o serviço gerenciado")
        conn = connection(engine)
        try:
            if engine == "postgres":
                with conn.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(username), sql.Literal(payload.password)))
                    cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(dbname), sql.Identifier(username)))
                    cursor.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
            else:
                q = chr(96)
                with conn.cursor() as cursor:
                    cursor.execute("CREATE DATABASE " + q + dbname + q + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    cursor.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", (username, payload.password))
                    mysql_grant(cursor, dbname, username, "owner")
        except (psycopg.Error, pymysql.MySQLError):
            raise HTTPException(409, "Falha ao criar banco. Verifique objetos parcialmente criados antes de tentar novamente.")
        finally:
            conn.close()
        db = {"engine": engine, "name": dbname, "owner": username,
              "users": [{"username": username, "permission": "owner", "password": payload.password}]}
        data["databases"].append(db)
        write_catalog(data)
        if payload.project:
            bind_internal(db, username, payload.project, projects)
        return {"engine": engine, "name": dbname, "owner": username, "project": payload.project}


def add_user(engine, dbname, payload):
    engine_ok(engine)
    valid(dbname)
    username = valid(payload.username)
    if payload.permission not in ("read_only", "read_write"):
        raise HTTPException(400, "Permissão não suportada")
    with LOCK:
        db = find(engine, dbname)
        if any(u["username"] == username for x in catalog()["databases"] if x["engine"] == engine for u in x["users"]):
            raise HTTPException(409, "Usuário já cadastrado nesse servidor")
        conn = connection(engine)
        try:
            if engine == "postgres":
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(username), sql.Literal(payload.password)))
                    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(username)))
                conn.close()
                conn = connection(engine, dbname)
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(username)))
                    grants = "SELECT" if payload.permission == "read_only" else "SELECT, INSERT, UPDATE, DELETE"
                    cur.execute(sql.SQL("GRANT " + grants + " ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(username)))
                    if payload.permission == "read_write":
                        cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(username)))
                    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT " + grants + " ON TABLES TO {}").format(sql.Identifier(db["owner"]), sql.Identifier(username)))
                    if payload.permission == "read_write":
                        cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(sql.Identifier(db["owner"]), sql.Identifier(username)))
            else:
                with conn.cursor() as cur:
                    cur.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", (username, payload.password))
                    mysql_grant(cur, dbname, username, payload.permission)
        except (psycopg.Error, pymysql.MySQLError):
            raise HTTPException(409, "Permissões não concluídas. Verifique se o usuário foi criado parcialmente.")
        finally:
            conn.close()
        data = catalog()
        for item in data["databases"]:
            if item["engine"] == engine and item["name"] == dbname:
                item["users"].append({"username": username, "permission": payload.permission,
                                      "password": payload.password})
                break
        write_catalog(data)
        return {"username": username, "permission": payload.permission}


def bind_internal(db, username, project, projects):
    if project not in projects:
        raise HTTPException(404, "Aplicação não encontrada")
    user = next((u for u in db["users"] if u["username"] == username), None)
    if not user:
        raise HTTPException(404, "Usuário não vinculado ao banco")
    # Docker apps need a separate host-gateway/network configuration. Do not
    # silently supply a broken 127.0.0.1 URL to a containerized application.
    if projects[project].get("runtime") == "docker":
        raise HTTPException(400, "Vínculo automático disponível para apps no host/PM2. Configure rede Docker antes de vincular containers.")
    port = ENGINES[db["engine"]]
    scheme = "postgresql" if db["engine"] == "postgres" else "mysql"
    url = (scheme + "://" + quote(username, safe="") + ":" + quote(user["password"], safe="")
           + "@127.0.0.1:" + str(port) + "/" + db["name"]
           + ("?schema=public" if db["engine"] == "postgres" else ""))
    project_root = ROOT / "projects"
    deploy_jobs.save_env(project_root, project, deploy_jobs.EnvChange(values={"DATABASE_URL": url}))
    return {"project": project, "variable": "DATABASE_URL", "host": "127.0.0.1"}


def bind(engine, name, payload, projects):
    with LOCK:
        return bind_internal(find(engine, name), payload.username, payload.project, projects)
