"""Multi-server node registry. Agent-initiated HTTPS heartbeat only (no RCE).

Tokens are shown once, stored as SHA-256 hashes. Every node has a distinct key.
Hosts/services are untrusted reported data: the server never executes them.
"""
import hashlib
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
            addresses_json TEXT NOT NULL DEFAULT '[]'
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
        if "addresses_json" not in columns:
            db.execute("ALTER TABLE nodes ADD COLUMN addresses_json TEXT NOT NULL DEFAULT '[]'")
        db.commit()
    os.chmod(NODES_DB, 0o600)


def _db():
    connect()
    conn = sqlite3.connect(str(NODES_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


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
        pm2 = _clean(body.pm2, ("name", "status", "pid", "restarts", "memory"))
        services = _clean(body.services, ("name", "status", "type"))
        db.execute("""UPDATE nodes SET last_seen=?,hostname=?,os_version=?,cpu_percent=?,
                   memory_percent=?,pm2_json=?,services_json=?,addresses_json=? WHERE id=?""",
                   (int(time.time()), body.hostname, body.os_version, body.cpu_percent,
                    body.memory_percent, json.dumps(pm2), json.dumps(services), json.dumps(addresses), node_id))
        db.commit()
    return {"ok": True}


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
