"""Windows Server -> Linux Manager outbound telemetry agent (stdlib only).

Configure LM_SERVER_URL, LM_NODE_ID, LM_NODE_TOKEN as Windows environment
variables. Run as a dedicated low-privilege account; no inbound port required.
"""
import json
import os
import platform
import socket
import ipaddress
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

CONFIG_PATH = Path(os.environ.get("LM_AGENT_CONFIG", str(Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "ServerManager" / "agent.json")))
try:
    CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else {}
except (ValueError, OSError):
    CONFIG = {}
BASE = (os.environ.get("LM_SERVER_URL") or CONFIG.get("server_url", "")).rstrip("/")
NODE = str(os.environ.get("LM_NODE_ID") or CONFIG.get("node_id", ""))
TOKEN = os.environ.get("LM_NODE_TOKEN") or CONFIG.get("token", "")
PM2_HOME = os.environ.get("LM_AGENT_PM2_HOME") or CONFIG.get("pm2_home", "")
PM2_COMMAND = os.environ.get("LM_AGENT_PM2_COMMAND") or CONFIG.get("pm2_command", "")
INTERVAL = max(20, int(os.environ.get("LM_AGENT_INTERVAL", "60")))


def run(command, seconds=8, env=None):
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=seconds, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pm2_processes():
    env = os.environ.copy()
    if PM2_HOME:
        env["PM2_HOME"] = PM2_HOME
    command = ["cmd.exe", "/d", "/c", PM2_COMMAND, "jlist"] if PM2_COMMAND else ["pm2", "jlist"]
    output = run(command, 12, env=env)
    try:
        data = json.loads(output)
        if not isinstance(data, list):
            return []
        return [{"name": str(p.get("name", ""))[:120],
                 "status": str(p.get("pm2_env", {}).get("status", ""))[:60],
                 "pid": str(p.get("pid", "")),
                 "restarts": str(p.get("pm2_env", {}).get("restart_time", "")),
                 "memory": str(p.get("monit", {}).get("memory", ""))}
                for p in data[:100] if isinstance(p, dict)]
    except ValueError:
        return []


def windows_services():
    # Read-only inventory: some XAMPP installs run Apache/MySQL without
    # Windows services, so their absence does not indicate they are stopped.
    script = (
        "$names = @('Apache2.4','Apache','mysql','mysql80','mariadb'); "
        "Get-Service -Name $names -ErrorAction SilentlyContinue | "
        "Select-Object Name,Status | ConvertTo-Json -Compress"
    )
    result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    services = []
    try:
        items = json.loads(result)
        if isinstance(items, dict):
            items = [items]
        services = [{"name": str(row.get("Name","")), "status": str(row.get("Status","")),
                     "type": "Windows service"} for row in items if isinstance(row, dict)]
    except (ValueError, TypeError):
        pass
    # XAMPP may start executables without registering Windows services.
    for executable, label in [("httpd.exe", "Apache / XAMPP"),
                               ("mysqld.exe", "MariaDB / XAMPP")]:
        name = executable[:-4]
        state = run(["tasklist", "/FI", "IMAGENAME eq " + executable, "/FO", "CSV", "/NH"])
        services.append({"name": label, "status": "Running" if executable.lower() in state.lower() else "Not detected",
                         "type": "XAMPP process"})
    php = os.path.join(os.environ.get("XAMPP_HOME", "C:\\xampp"), "php", "php.exe")
    if os.path.isfile(php):
        version = run([php, "-v"]).splitlines()
        services.append({"name": "PHP", "status": version[0][:120] if version else "Installed",
                         "type": "XAMPP runtime"})
    return services


def network_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            address = info[4][0].split("%")[0]
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast:
                addresses.add(str(ip))
    except (OSError, ValueError):
        pass
    return sorted(addresses)[:16]


def send():
    if not BASE.startswith("https://") or not NODE.isdigit() or not TOKEN:
        raise RuntimeError("Defina LM_SERVER_URL=https://..., LM_NODE_ID e LM_NODE_TOKEN")
    report = {"hostname": platform.node() or "windows",
              "os_version": platform.platform()[:200],
              "pm2": pm2_processes(), "services": windows_services(),
              "addresses": network_addresses()}
    data = json.dumps(report).encode("utf-8")
    request = urllib.request.Request(
        BASE + "/api/agent/nodes/" + NODE + "/heartbeat", data=data,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status


def run_forever(stop_event=None):
    while stop_event is None or not stop_event.is_set():
        try:
            print("Server Manager:", send(), flush=True)
        except Exception as error:
            print("Falha ao reportar status:", type(error).__name__, flush=True)
        if stop_event is not None:
            stop_event.wait(INTERVAL)
        else:
            time.sleep(INTERVAL)


if __name__ == "__main__":
    run_forever()
