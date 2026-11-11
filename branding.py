"""Brand image assets for Linux Manager.

Admin-only updates. Never serve SVG/HTML or user-supplied filenames.
Only signature-checked raster images are served from a private directory.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import struct
import threading
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get("PROJECTS_DIR", "/srv/linux-manager/projects")).resolve().parent / "branding"
LOCK = threading.RLock()
MAX_BYTES = 1024 * 1024  # 1 MiB
TYPES = {
    "png": "image/png", "jpeg": "image/jpeg",
    "webp": "image/webp", "ico": "image/x-icon",
}
VALID = {"logo": {"png", "jpeg", "webp"},
         "favicon": {"png", "webp", "ico"}}


class AssetUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=1, max_length=1500000)


def _directory():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.is_symlink():
        raise HTTPException(500, "Diretório de identidade visual inseguro")
    os.chmod(ROOT, 0o700)


def _manifest():
    _directory()
    file = ROOT / "manifest.json"
    if not file.is_file():
        return {}
    if file.is_symlink():
        raise HTTPException(500, "Manifesto de identidade visual inseguro")
    try:
        manifest = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError()
        return {k: v for k, v in manifest.items() if k in VALID and v in TYPES and v in VALID[k]}
    except (ValueError, OSError):
        raise HTTPException(500, "Manifesto de identidade visual inválido")


def _atomic(path, content):
    tmp = path.with_name(path.name + ".tmp")
    if tmp.is_symlink():
        raise HTTPException(500, "Arquivo temporário inseguro")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        tmp.unlink(missing_ok=True)


def _size_png(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return False
    w, h = struct.unpack(">II", data[16:24])
    return 0 < w <= 4096 and 0 < h <= 4096


def _valid_bytes(data, ext):
    if ext == "png":
        return _size_png(data)
    if ext == "jpeg":
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if ext == "webp":
        return (len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
                and data[12:16] in (b"VP8 ", b"VP8L", b"VP8X"))
    if ext == "ico":
        if len(data) < 22 or data[:4] != b"\x00\x00\x01\x00":
            return False
        count = int.from_bytes(data[4:6], "little")
        return 1 <= count <= 16
    return False


def metadata():
    with LOCK:
        manifest = _manifest()
        result = {}
        for kind in VALID:
            ext = manifest.get(kind)
            file = ROOT / f"{kind}.{ext}" if ext else None
            if file and file.is_file() and not file.is_symlink():
                fingerprint = hashlib.sha256(file.read_bytes()).hexdigest()[:16]
                result[kind] = f"/branding/{kind}?v={fingerprint}"
            else:
                result[kind] = None
        return result


def asset(kind):
    if kind not in VALID:
        raise HTTPException(404, "Imagem não encontrada")
    with LOCK:
        ext = _manifest().get(kind)
        if not ext:
            raise HTTPException(404, "Imagem não configurada")
        path = ROOT / f"{kind}.{ext}"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "Imagem não encontrada")
        return path, TYPES[ext]


def save(kind, payload):
    if kind not in VALID:
        raise HTTPException(400, "Tipo de imagem inválido")
    ext = payload.filename.rsplit(".", 1)[-1].lower()
    if ext == "jpg":
        ext = "jpeg"
    if ext not in VALID[kind]:
        raise HTTPException(400, "Formato não permitido. Utilize PNG, JPEG ou WebP para logo; PNG, WebP ou ICO para favicon.")
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Conteúdo de imagem inválido")
    if not data or len(data) > MAX_BYTES or not _valid_bytes(data, ext):
        raise HTTPException(400, "Imagem inválida ou maior que 1 MB")
    with LOCK:
        manifest = _manifest()
        _atomic(ROOT / f"{kind}.{ext}", data)
        old_ext = manifest.get(kind)
        manifest[kind] = ext
        _atomic(ROOT / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        if old_ext and old_ext != ext:
            (ROOT / f"{kind}.{old_ext}").unlink(missing_ok=True)
    return metadata()


def reset(kind):
    if kind not in VALID:
        raise HTTPException(400, "Tipo de imagem inválido")
    with LOCK:
        manifest = _manifest()
        old = manifest.pop(kind, None)
        _atomic(ROOT / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        if old:
            (ROOT / f"{kind}.{old}").unlink(missing_ok=True)
    return metadata()
