"""Windows Server -> Linux Manager outbound telemetry agent (stdlib only).

Configure LM_SERVER_URL, LM_NODE_ID, LM_NODE_TOKEN as Windows environment
variables. Run as a dedicated low-privilege account; no inbound port required.
"""
import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request

BASE = os.environ.get("LM_SERVER_URL", "").rstrip("/")
NODE = os.environ.get("LM_NODE_ID", "")
TOKEN = os.environ.get("LM_NODE_TOKEN", "")
INTERVAL = max(20, int(os.environ.get("LM_AGENT_INTERVAL", "60")))


def run(command, seconds=8):
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=seconds, creationflags=subprocess.CREATE_NO_WINDOW)
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pm2_processes():
    output = run(["pm2", "jlist"], 12)
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


def send():
    if not BASE.startswith("https://") or not NODE.isdigit() or not TOKEN:
        raise RuntimeError("Defina LM_SERVER_URL=https://..., LM_NODE_ID e LM_NODE_TOKEN")
    report = {"hostname": platform.node() or "windows",
              "os_version": platform.platform()[:200],
              "pm2": pm2_processes(), "services": windows_services()}
    data = json.dumps(report).encode("utf-8")
    request = urllib.request.Request(
        BASE + "/api/agent/nodes/" + NODE + "/heartbeat", data=data,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status


if __name__ == "__main__":
    while True:
        try:
            print("Linux Manager:", send(), flush=True)
        except Exception as error:
            # Never log bearer token or response request headers.
            print("Falha ao reportar status:", type(error).__name__, flush=True)
        time.sleep(INTERVAL)
