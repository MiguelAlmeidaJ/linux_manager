"""Restricted Windows command executor for server-agent typed one-shot jobs.

Never evaluates code or opens an inbound control port. Only exact PM2 entries
visible to the configured PM2 daemon and explicitly registered Apache/MySQL
Windows services are manageable. The controller never sends shell commands.
"""
import json
import os
import re
from pathlib import Path
import subprocess

PROCESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
SERVICE_NAMES = frozenset(("apache", "apache2.4", "mysql", "mysql80", "mariadb"))
PM2_ACTIONS = frozenset(("start", "stop", "restart", "reload", "logs"))


def _run(command, env=None, timeout=30):
    proc = subprocess.run(command, shell=False, capture_output=True,
                          text=True, errors="replace", timeout=timeout,
                          env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode == 0, output[-10000:]


def perform(action, pm2_home="", pm2_command=""):
    identifier = action.get("id")
    kind = action.get("kind")
    target = action.get("target")
    result = {"id": identifier, "ok": False, "output": "Operação não realizada"}
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        return result
    if not isinstance(target, str) or not PROCESS_RE.fullmatch(target):
        result["output"] = "Identificador de serviço/processo inválido"
        return result
    try:
        if kind in PM2_ACTIONS:
            if not pm2_command or not os.path.isfile(pm2_command):
                raise RuntimeError("Caminho do PM2 não configurado no serviço Windows")
            env = os.environ.copy()
            if pm2_home:
                env["PM2_HOME"] = pm2_home
            ok, raw = _run([pm2_command, "jlist"], env=env, timeout=15)
            if not ok:
                raise RuntimeError("Não foi possível consultar o daemon PM2")
            processes = json.loads(raw)
            if not isinstance(processes, list) or target not in {
                p.get("name") for p in processes if isinstance(p, dict)
            }:
                raise RuntimeError("Processo não encontrado no PM2 local")
            # 'reload' is a PM2 reload, not a Git update or deployment.
            args = ["logs", target, "--nostream", "--lines", "80", "--raw"] if kind == "logs" else [kind, target]
            ok, output = _run([pm2_command, *args], env=env, timeout=35)
        elif kind == "service_logs":
            if target not in ("apache", "mysql"):
                raise RuntimeError("Logs disponíveis apenas para Apache e MySQL")
            root = Path(os.environ.get("XAMPP_HOME", r"C:\xampp"))
            if target == "apache":
                paths = [root / "apache" / "logs" / "error.log"]
            else:
                directory = root / "mysql" / "data"
                paths = list(directory.glob("*.err"))[:6] if directory.is_dir() else []
                paths += [root / "mysql" / "data" / "mysql_error.log"]
            outputs = []
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    with path.open("rb") as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell()-16000))
                        chunk = stream.read(16000).decode("utf-8", errors="replace")
                    outputs.append(str(path.name) + "\n" + chunk[-6000:])
                except OSError:
                    continue
            ok = bool(outputs)
            output = "\n\n".join(outputs)[-10000:] if outputs else "Arquivo de log do XAMPP não localizado"
        elif kind == "service_restart":
            if target.lower() not in SERVICE_NAMES:
                raise RuntimeError("Somente serviços Apache/MySQL podem ser reiniciados")
            # Use built-in cmdlet with a literal allowlisted name, never shell text.
            ps = ("$ErrorActionPreference='Stop'; "
                  "$svc=Get-Service -Name '" + target + "' -ErrorAction Stop; "
                  "if($svc.Status -eq 'Running'){Restart-Service -Name '" + target +
                  "' -Force -ErrorAction Stop}else{Start-Service -Name '" + target +
                  "' -ErrorAction Stop}; "
                  "(Get-Service -Name '" + target + "').Status")
            ok, output = _run(["powershell.exe", "-NoProfile", "-NonInteractive",
                               "-Command", ps], timeout=55)
        else:
            raise RuntimeError("Tipo de operação não permitido")
        result["ok"] = bool(ok)
        result["output"] = output[-10000:] if output else ("Operação concluída" if ok else "Falha ao executar a ação")
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, RuntimeError) as exc:
        # Avoid untrusted exception content in transmitted logs.
        result["output"] = str(exc)[:300] if isinstance(exc, RuntimeError) else "Falha na execução no Windows"
    return result
