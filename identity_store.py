"""SQLite identity and append-only-at-API audit for the Linux Manager.

Single-server mode: OS user linuxmanager retains filesystem access; shell/ Docker
are privileged and are *not* security isolated by web roles. Never store raw
passwords, cookies, request bodies or .env values in the audit table.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(os.environ.get("AUTH_DATABASE_PATH",
                  str(Path(os.environ.get("PROJECTS_DIR", "/srv/linux-manager/projects")).resolve().parent / "auth" / "manager-auth.sqlite3")))
DB_LOCK = threading.RLock()
USERNAME = re.compile(r"^[a-z][a-z0-9._-]{2,39}$")
MAIL = re.compile(r"^[^\s@]{1,100}@[^\s@]{1,120}\.[^\s@]{2,40}$")
LOGIN_ATTEMPTS = {}
RATE_LOCK = threading.Lock()
RATE_WINDOW = 900
RATE_LIMIT = 8


class NewUser(BaseModel):
    username: str
    email: str
    password: str = Field(min_length=12, max_length=256)
    role: str = "user"


class EditUser(BaseModel):
    username: str
    email: str
    role: str
    active: bool
    password: str | None = Field(default=None, min_length=12, max_length=256)


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if DB_PATH.parent.is_symlink() or DB_PATH.is_symlink():
        raise HTTPException(500, "Banco de autenticação em caminho inseguro")
    os.chmod(DB_PATH.parent, 0o700)
    if DB_PATH.is_symlink():
        raise HTTPException(500, "Banco de autenticação em caminho inseguro")
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    if DB_PATH.exists():
        os.chmod(DB_PATH, 0o600)
    return conn


def secret_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$" + salt.hex() + "$" + result.hex()


def password_ok(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, digest = stored.split("$")
        if algorithm != "scrypt" or int(rounds) != 16384:
            return False
        result = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(result, bytes.fromhex(digest))
    except (ValueError, TypeError, OverflowError):
        return False


def init(bootstrap_password: str):
    with DB_LOCK, db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            session_version INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            status INTEGER NOT NULL,
            ip TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at DESC, id DESC);
        """)
        count = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
        if count == 0:
            if len(bootstrap_password) < 12:
                raise RuntimeError("ADMIN_PASSWORD precisa de pelo menos 12 caracteres para migrar para autenticação por usuário. Atualize o .env antes de reiniciar.")
            email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@localhost.invalid").lower()
            if not MAIL.fullmatch(email):
                raise RuntimeError("BOOTSTRAP_ADMIN_EMAIL inválido")
            now = int(time.time())
            cursor = conn.execute("INSERT INTO users(username,email,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,'admin',1,?,?)",
                                  ("admin", email, secret_hash(bootstrap_password), now, now))
            conn.execute("INSERT INTO audit(created_at,user_id,username,action,resource,status,ip) VALUES(?,?,?,?,?,?,?)",
                         (now, cursor.lastrowid, "admin", "system.bootstrap", "auth", 200, "local"))
        conn.commit()


def _validate_user(username: str, email: str, role: str):
    username = username.strip().lower()
    email = email.strip().lower()
    if not USERNAME.fullmatch(username):
        raise HTTPException(400, "Usuário: 3 a 40 caracteres, iniciando por letra, usando letras minúsculas, números, ponto, _ ou -")
    if not MAIL.fullmatch(email):
        raise HTTPException(400, "E-mail inválido")
    if role not in ("admin", "user"):
        raise HTTPException(400, "Tipo deve ser admin ou user")
    return username, email


def public(row):
    return {key: row[key] for key in ("id", "username", "email", "role", "active", "created_at", "updated_at", "session_version")}


def get_user(uid):
    if not isinstance(uid, int):
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row is not None else None


def authenticate(username: str, password: str, ip: str):
    username = username.strip().lower()
    identifier = username[:100]
    now = time.monotonic()
    with RATE_LOCK:
        attempts = LOGIN_ATTEMPTS.get(ip + ":" + identifier, [])
        attempts = [x for x in attempts if now - x < RATE_WINDOW]
        if len(attempts) >= RATE_LIMIT:
            raise HTTPException(429, "Muitas tentativas. Tente novamente mais tarde.")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE", (username, username)).fetchone()
        # Keep expensive password verification even for unknown accounts.
        ok = password_ok(password, row["password_hash"] if row else DUMMY_HASH)
        if not row or not ok or not row["active"]:
            with RATE_LOCK:
                LOGIN_ATTEMPTS[ip + ":" + identifier] = attempts + [now]
            log(None, identifier or "desconhecido", "auth.login_failed", "login", 401, ip)
            raise HTTPException(401, "Credenciais inválidas")
    with RATE_LOCK:
        LOGIN_ATTEMPTS.pop(ip + ":" + identifier, None)
    return dict(row)


def list_users():
    with db() as conn:
        return [public(row) for row in conn.execute("SELECT * FROM users ORDER BY active DESC, username")]


def create_user(payload: NewUser):
    username, email = _validate_user(payload.username, payload.email, payload.role)
    now = int(time.time())
    with DB_LOCK, db() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,email,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                (username, email, secret_hash(payload.password), payload.role, now, now))
            conn.commit()
            return public(conn.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone())
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Nome de usuário ou e-mail já cadastrado")


def edit_user(uid: int, payload: EditUser, actor_id: int):
    username, email = _validate_user(payload.username, payload.email, payload.role)
    with DB_LOCK, db() as conn:
        existing = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not existing:
            raise HTTPException(404, "Usuário não encontrado")
        if uid == actor_id and (not payload.active or payload.role != "admin"):
            raise HTTPException(409, "Não é possível desativar ou rebaixar sua própria conta")
        if existing["role"] == "admin" and existing["active"] and (not payload.active or payload.role != "admin"):
            admins = conn.execute("SELECT count(*) AS n FROM users WHERE role='admin' AND active=1").fetchone()["n"]
            if admins < 2:
                raise HTTPException(409, "É obrigatório manter pelo menos um administrador ativo")
        values = [username, email, payload.role, int(payload.active), int(time.time())]
        revoke_session = (payload.password is not None or payload.role != existing["role"] or
                          int(payload.active) != existing["active"])
        sql = "UPDATE users SET username=?,email=?,role=?,active=?,updated_at=?"
        if revoke_session:
            sql += ",session_version=session_version+1"
        if payload.password is not None:
            sql += ",password_hash=?"
            values.append(secret_hash(payload.password))
        sql += " WHERE id=?"
        values.append(uid)
        try:
            conn.execute(sql, values)
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Nome de usuário ou e-mail já cadastrado")
        return public(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def log(user_id, username, action, resource, status, ip):
    # Restrict log parameters to primitive metadata, not content from request bodies.
    with DB_LOCK, db() as conn:
        conn.execute("INSERT INTO audit(created_at,user_id,username,action,resource,status,ip) VALUES(?,?,?,?,?,?,?)",
                     (int(time.time()), user_id, str(username)[:100], str(action)[:96],
                      str(resource)[:256], int(status), str(ip)[:80]))
        conn.commit()


def list_audit(limit: int = 100, offset: int = 0):
    limit = min(max(limit, 1), 250)
    offset = max(offset, 0)
    with db() as conn:
        rows = conn.execute("SELECT id,created_at,user_id,username,action,resource,status,ip FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) AS total FROM audit").fetchone()["total"]
    return {"items": [dict(r) for r in rows], "total": total}


# Make enumeration of unknown usernames less distinguishable.
DUMMY_HASH = secret_hash("placeholder-nonworking-password")
