"""GitHub SSH identity for one Linux Manager installation.

The private key never leaves the server; GitHub's ED25519 host key is pinned
to the published value (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
"""
import os
import subprocess
from pathlib import Path
from fastapi import HTTPException

GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
GITHUB_FINGERPRINT = "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"


def ssh_directory():
    home = Path.home().resolve()
    folder = home / ".ssh"
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or folder.resolve() != folder:
        raise HTTPException(400, "Diretório SSH inseguro")
    os.chmod(folder, 0o700)
    return folder


def key_path():
    return ssh_directory() / "linux_manager_github_ed25519"


def ensure_known_hosts():
    folder = ssh_directory()
    known = folder / "linux_manager_known_hosts"
    if known.is_symlink():
        raise HTTPException(400, "Arquivo de hosts inválido")
    if not known.exists() or known.read_text(encoding="utf-8") != GITHUB_HOST_KEY + "\n":
        known.write_text(GITHUB_HOST_KEY + "\n", encoding="utf-8")
    os.chmod(known, 0o600)
    return known


def public_key():
    key = key_path()
    pub = Path(str(key) + ".pub")
    if key.is_symlink() or pub.is_symlink():
        raise HTTPException(400, "Chave SSH inválida")
    if not key.is_file() or not pub.is_file():
        return None
    return pub.read_text(encoding="utf-8").strip()


def generate_key():
    key = key_path()
    if key.exists() or Path(str(key) + ".pub").exists():
        if public_key():
            return public_key()
        raise HTTPException(409, "Há uma chave parcial no servidor. Corrija manualmente antes de gerar.")
    ensure_known_hosts()
    try:
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                        "linux-manager@server", "-f", str(key)],
                       check=True, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, "Não foi possível gerar a chave SSH")
    os.chmod(key, 0o600)
    os.chmod(str(key) + ".pub", 0o644)
    return public_key()


def git_environment(existing=None):
    env = dict(existing if existing is not None else os.environ)
    env.pop("ADMIN_PASSWORD", None)
    env.pop("SESSION_SECRET", None)
    # This explicit command applies to both Git clone and Git fetch/pull.
    # StrictHostKeyChecking and the pinned GitHub key avoid TOFU on first use.
    if public_key():
        kh = ensure_known_hosts()
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path()} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={kh} "
            f"-o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=10"
        )
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def status():
    pub = public_key()
    return {"configured": bool(pub), "public_key": pub,
            "host": "github.com", "host_fingerprint": GITHUB_FINGERPRINT}


def test_repository(repo):
    if not (repo.startswith("git@github.com:") or repo.startswith("ssh://git@github.com/")):
        raise HTTPException(400, "Teste SSH disponível apenas para repositórios github.com")
    if not public_key():
        raise HTTPException(400, "Gere a chave pública primeiro")
    try:
        completed = subprocess.run(["git", "ls-remote", "--heads", repo],
                                   env=git_environment(), capture_output=True,
                                   text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(408, "Não foi possível conectar ao GitHub")
    if completed.returncode:
        # Do not reflect potentially sensitive error output or URLs.
        raise HTTPException(400, "Acesso negado. Adicione a chave pública em Deploy keys (read-only) e confira o endereço do repositório.")
    return {"ok": True, "branches": [
        line.split("refs/heads/", 1)[1] for line in completed.stdout.splitlines()
        if "refs/heads/" in line
    ][:50]}
