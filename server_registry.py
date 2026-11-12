"""Multi-server node registry. Agent-initiated HTTPS heartbeat only (no RCE).

Tokens are shown once, stored as SHA-256 hashes. Every node has a distinct key.
Hosts/services are untrusted reported data: the server never executes them.
"""
import hashlib
from contextlib import contextmanager
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field
import identity_store as identity

_LOCK = threading.RLock()
NODES_DB = identity.DB_PATH.parent / "nodes.sqlite3"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:-]{1,79}$")
MAX_AGE = 180


class NodeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    os_type: str = "windows"


class Heartbeat(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    os_version: str = Field(default="", max_length=200)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    memory_percent: float | None = Field(default=None, ge=0, le=100)
    pm2: list[dict] = Field(default_factory=list, max_length=100)
    services: list[dict] = Field(default_factory=list, max_length=40)
    addresses: list[str] = Field(default_factory=list, max_length=16)
    php_projects: list[dict] = Field(default_factory=list, max_length=100)
    mysql: dict = Field(default_factory=dict)
    performance: dict = Field(default_factory=dict)
    action_result: dict | None = Field(default=None)


def connect():
    NODES_DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if NODES_DB.is_symlink() or NODES_DB.parent.is_symlink():
        raise HTTPException(500, "Caminho inseguro para registro de servidores")
    with sqlite3.connect(str(NODES_DB), timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            os_type TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            last_seen INTEGER,
            hostname TEXT NOT NULL DEFAULT '',
            os_version TEXT NOT NULL DEFAULT '',
            cpu_percent REAL,
            memory_percent REAL,
            pm2_json TEXT NOT NULL DEFAULT '[]',
            services_json TEXT NOT NULL DEFAULT '[]',
            addresses_json TEXT NOT NULL DEFAULT '[]',
            php_projects_json TEXT NOT NULL DEFAULT '[]',
            mysql_json TEXT NOT NULL DEFAULT '{}',
            performance_json TEXT NOT NULL DEFAULT '{}'
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
        if "addresses_json" not in columns:
            db.execute("ALTER TABLE nodes ADD COLUMN addresses_json TEXT NOT NULL DEFAULT '[]'")
        if "php_projects_json" not in columns:
            db.execute("ALTER TABLE nodes ADD COLUMN php_projects_json TEXT NOT NULL DEFAULT '[]'")
        if "mysql_json" not in columns:
            db.execute("ALTER TABLE nodes ADD COLUMN mysql_json TEXT NOT NULL DEFAULT '{}'")
        if "performance_json" not in columns:
            db.execute("ALTER TABLE nodes ADD COLUMN performance_json TEXT NOT NULL DEFAULT '{}'")
        db.execute("""CREATE TABLE IF NOT EXISTS remote_actions (
            id TEXT PRIMARY KEY, node_id INTEGER NOT NULL,
            kind TEXT NOT NULL, target TEXT NOT NULL,
            state TEXT NOT NULL, created_at INTEGER NOT NULL,
            delivered_at INTEGER, finished_at INTEGER,
            result TEXT NOT NULL DEFAULT '',
            requested_by TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS node_metrics (
            node_id INTEGER NOT NULL, recorded_at INTEGER NOT NULL,
            cpu_percent REAL NOT NULL, memory_percent REAL NOT NULL,
            PRIMARY KEY(node_id,recorded_at)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS node_metrics_at ON node_metrics(recorded_at)")
        db.execute("CREATE INDEX IF NOT EXISTS remote_actions_node ON remote_actions(node_id,created_at)")
        db.commit()
    os.chmod(NODES_DB, 0o600)


@contextmanager
def _db():
    connect()
    conn = sqlite3.connect(str(NODES_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def create(data):
    if not NAME_RE.fullmatch(data.name) or data.os_type not in ("windows", "linux"):
        raise HTTPException(400, "Nome ou sistema operacional inválido")
    token = secrets.token_urlsafe(48)
    with _LOCK, _db() as db:
        try:
            cursor = db.execute(
                "INSERT INTO nodes(name,os_type,token_hash,created_at) VALUES (?,?,?,?)",
                (data.name, data.os_type, hashlib.sha256(token.encode()).hexdigest(), int(time.time())))
            db.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Já existe um servidor com esse nome")
    return {"id": cursor.lastrowid, "name": data.name, "os_type": data.os_type,
            "token": token, "message": "Guarde o token agora; ele não será exibido novamente."}


def _public(row, include_detail=False):
    item = {"id": row["id"], "name": row["name"], "os_type": row["os_type"],
            "hostname": row["hostname"], "os_version": row["os_version"],
            "enabled": bool(row["enabled"]), "last_seen": row["last_seen"],
            "online": bool(row["enabled"] and row["last_seen"] and int(time.time())-row["last_seen"] < MAX_AGE),
            "cpu_percent": row["cpu_percent"], "memory_percent": row["memory_percent"],
            "addresses": json.loads(row["addresses_json"])}
    if include_detail:
        item["pm2"] = json.loads(row["pm2_json"])
        item["services"] = json.loads(row["services_json"])
        item["php_projects"] = json.loads(row["php_projects_json"])
        item["mysql"] = json.loads(row["mysql_json"])
        item["performance"] = json.loads(row["performance_json"])
    return item


def listing():
    with _db() as db:
        return [_public(row) for row in db.execute("SELECT * FROM nodes ORDER BY name")]


def detail(node_id):
    with _db() as db:
        row = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Servidor não encontrado")
        return _public(row, True)


def _clean_ports(items):
    result = []
    for item in items[:32] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port", 0))
            address = str(ipaddress.ip_address(str(item.get("address", "")).split("%")[0]))
            if 1 <= port <= 65535 and (port, address) not in {(p["port"], p["address"]) for p in result}:
                result.append({"port": port, "address": address})
        except (ValueError, TypeError):
            continue
    return result


def _clean(items, attributes):
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned.append({k: str(item.get(k, ""))[:180] for k in attributes})
    return cleaned


def verify_token(node_id, bearer):
    if not bearer or len(bearer) > 160:
        raise HTTPException(401, "Credencial inválida")
    with _db() as db:
        row = db.execute("SELECT token_hash,enabled,os_type FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row or not row["enabled"] or not secrets.compare_digest(hashlib.sha256(bearer.encode()).hexdigest(), row["token_hash"]):
            raise HTTPException(401, "Credencial inválida")
        return row["os_type"]


def _performance(payload):
    """Allowlist numeric Windows metrics; discard arbitrary/untrusted fields."""
    def positive(value, limit=2**50):
        try:
            n = float(value)
            return round(n, 1) if 0 <= n <= limit and n == n else 0
        except (ValueError, TypeError, OverflowError):
            return 0
    top = []
    for row in (payload.get("top_processes") or [])[:30] if isinstance(payload.get("top_processes"), list) else []:
        if isinstance(row, dict):
            top.append({"name": str(row.get("name", ""))[:90],
                        "pid": int(positive(row.get("pid"), 2**31)),
                        "cpu_percent": positive(row.get("cpu_percent"), 100),
                        "memory_bytes": int(positive(row.get("memory_bytes")))})
    disks = []
    for row in (payload.get("disks") or [])[:12] if isinstance(payload.get("disks"), list) else []:
        if isinstance(row, dict):
            disks.append({"name": str(row.get("name", ""))[:10],
                          "size": int(positive(row.get("size"))),
                          "free": int(positive(row.get("free")))})
    return {"cpu_percent": positive(payload.get("cpu_percent"), 100),
            "memory_percent": positive(payload.get("memory_percent"), 100),
            "memory_total": int(positive(payload.get("memory_total"))),
            "memory_used": int(positive(payload.get("memory_used"))),
            "top_processes": top, "disks": disks}


ACTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
VALID_ACTIONS = frozenset(("start", "stop", "restart", "reload", "logs"))
ALLOWED_XAMPP_SERVICES = frozenset(("apache", "apache2.4", "mysql", "mysql80", "mariadb"))


def queue_action(node_id, kind, target, actor):
    """Queue one typed operation; no arbitrary code or unmanaged process names."""
    if kind not in VALID_ACTIONS and kind not in ("service_restart", "service_logs"):
        raise HTTPException(400, "Ação remota não permitida")
    with _LOCK, _db() as db:
        node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node or not node["enabled"] or node["os_type"] != "windows":
            raise HTTPException(404, "Windows Server não cadastrado")
        now = int(time.time())
        if not node["last_seen"] or now - node["last_seen"] > MAX_AGE:
            raise HTTPException(409, "Agente offline; nenhuma ação será enviada")
        if kind == "service_logs":
            if target not in ("apache","mysql"):
                raise HTTPException(400, "Logs disponíveis somente para Apache ou MySQL do XAMPP")
        elif kind == "service_restart":
            names = {p.get("name", "").lower() for p in json.loads(node["services_json"])
                     if p.get("type") == "Windows service"}
            if target.lower() not in ALLOWED_XAMPP_SERVICES or target.lower() not in names:
                raise HTTPException(400, "Serviço XAMPP não está registrado como serviço Windows")
        else:
            if not ACTION_NAME_RE.fullmatch(target):
                raise HTTPException(400, "Nome do processo PM2 inválido")
            known = {p.get("name") for p in json.loads(node["pm2_json"])}
            if target not in known:
                raise HTTPException(404, "Processo não reportado pelo agente")
        pending = db.execute("SELECT COUNT(*) FROM remote_actions WHERE node_id=? AND state IN ('queued','delivered')",
                             (node_id,)).fetchone()[0]
        if pending:
            raise HTTPException(409, "Outra ação está em execução ou aguardando confirmação")
        identifier = secrets.token_hex(16)
        db.execute("INSERT INTO remote_actions(id,node_id,kind,target,state,created_at,requested_by) VALUES(?,?,?,?,?,?,?)",
                   (identifier, node_id, kind, target, "queued", now, str(actor)[:100]))
    return {"id": identifier, "status": "queued", "kind": kind, "target": target}


def metrics_history(node_id):
    with _db() as db:
        rows = db.execute("SELECT recorded_at,cpu_percent,memory_percent FROM node_metrics "
                          "WHERE node_id=? ORDER BY recorded_at DESC LIMIT 120", (node_id,)).fetchall()
        return [dict(row) for row in reversed(rows)]


def list_actions(node_id, limit=20):
    with _db() as db:
        return [dict(row) for row in db.execute(
            "SELECT id,kind,target,state,created_at,finished_at,result,requested_by "
            "FROM remote_actions WHERE node_id=? ORDER BY created_at DESC LIMIT ?",
            (node_id, min(max(1,limit),50))).fetchall()]


def _deliver_action(db, node_id, now):
    db.execute("UPDATE remote_actions SET state='expired',finished_at=?,result='Sem confirmação do agente' "
               "WHERE node_id=? AND state='delivered' AND delivered_at < ?", (now,node_id,now-120))
    db.execute("UPDATE remote_actions SET state='expired',finished_at=?,result='Ação expirou na fila' "
               "WHERE node_id=? AND state='queued' AND created_at < ?", (now,node_id,now-300))
    row = db.execute("SELECT id,kind,target FROM remote_actions WHERE node_id=? AND state='queued' "
                     "ORDER BY created_at LIMIT 1", (node_id,)).fetchone()
    if row:
        db.execute("UPDATE remote_actions SET state='delivered',delivered_at=? WHERE id=?", (now,row["id"]))
        return dict(row)
    return None


def _receive_result(db, node_id, report, now):
    if not isinstance(report, dict):
        return
    identifier = report.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        return
    state = "done" if report.get("ok") is True else "error"
    result = str(report.get("output") or "")[:12000]
    result = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result)
    result = "".join(ch for ch in result if ch in "\n\r\t" or ord(ch) >= 32)
    db.execute("UPDATE remote_actions SET state=?,finished_at=?,result=? "
               "WHERE id=? AND node_id=? AND state='delivered'",
               (state,now,result,identifier,node_id))


def heartbeat(node_id, bearer, body):
    if not bearer.startswith("Bearer ") or len(bearer) > 200:
        raise HTTPException(401, "Token inválido")
    token = bearer[7:]
    verify_token(node_id, token)
    addresses = []
    for address in body.addresses:
        try:
            ip = ipaddress.ip_address(address.strip())
            if ip.is_global or ip.is_private or ip.is_link_local:
                if not ip.is_loopback and not ip.is_multicast and not ip.is_unspecified and str(ip) not in addresses:
                    addresses.append(str(ip))
        except ValueError:
            continue
    with _LOCK, _db() as db:
        _receive_result(db,node_id,body.action_result,int(time.time()))
        pm2 = _clean(body.pm2, ("name", "status", "pid", "restarts", "memory"))
        for cleaned, original in zip(pm2, [x for x in body.pm2 if isinstance(x, dict)]):
            cleaned["ports"] = _clean_ports(original.get("ports", []))
        services = _clean(body.services, ("name", "status", "type"))
        php = _clean(body.php_projects, ("name", "path", "type", "source"))
        mysql = body.mysql if isinstance(body.mysql, dict) else {}
        status = str(mysql.get("status", "unavailable"))
        if status not in ("ok", "unavailable", "configuration_required", "not_installed"):
            status = "unavailable"
        performance = _performance(body.performance)
        mysql_clean = {"status": status, "engine": "MySQL/MariaDB",
            "databases": _clean(mysql.get("databases", [])[:100] if isinstance(mysql.get("databases"), list) else [], ("name",))}
        db.execute("""UPDATE nodes SET last_seen=?,hostname=?,os_version=?,cpu_percent=?,
                   memory_percent=?,pm2_json=?,services_json=?,addresses_json=?,php_projects_json=?,mysql_json=?,performance_json=? WHERE id=?""",
                   (int(time.time()), body.hostname, body.os_version, body.cpu_percent,
                    body.memory_percent, json.dumps(pm2), json.dumps(services), json.dumps(addresses),
                    json.dumps(php), json.dumps(mysql_clean), json.dumps(performance), node_id))
        if body.cpu_percent is not None and body.memory_percent is not None:
            db.execute("INSERT OR REPLACE INTO node_metrics(node_id,recorded_at,cpu_percent,memory_percent) VALUES (?,?,?,?)",
                       (node_id,int(time.time()),body.cpu_percent,body.memory_percent))
            db.execute("DELETE FROM node_metrics WHERE node_id=? AND recorded_at < ?",
                       (node_id,int(time.time())-86400))
        command = _deliver_action(db,node_id,int(time.time()))
        db.commit()
    return {"ok": True, "action": command}


def rotate_token(node_id):
    """Reissue enrollment token; previous agent loses access immediately."""
    token = secrets.token_urlsafe(48)
    with _LOCK, _db() as db:
        row = db.execute("SELECT name,os_type,enabled FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Servidor não encontrado")
        if not row["enabled"]:
            raise HTTPException(409, "Servidor desativado")
        db.execute("UPDATE nodes SET token_hash=? WHERE id=?",
                   (hashlib.sha256(token.encode()).hexdigest(), node_id))
        db.commit()
    return {"id": node_id, "name": row["name"], "os_type": row["os_type"],
            "token": token, "message": "Token anterior revogado. Baixe o instalador novamente."}


def disable(node_id):
    with _LOCK, _db() as db:
        cur = db.execute("UPDATE nodes SET enabled=0,token_hash=? WHERE id=?",
                         (hashlib.sha256(secrets.token_bytes(64)).hexdigest(), node_id))
        db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Servidor não encontrado")
    return {"ok": True}
