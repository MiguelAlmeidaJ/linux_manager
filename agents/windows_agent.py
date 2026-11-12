"""Windows Server -> Linux Manager outbound telemetry agent (stdlib only).

Configure LM_SERVER_URL, LM_NODE_ID, LM_NODE_TOKEN as Windows environment
variables. Run as a dedicated low-privilege account; no inbound port required.
"""
import json
import os
import platform
import socket
import ipaddress
import re
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


def windows_pm2_ports(processes):
    """Find TCP LISTEN sockets belonging to PM2 PIDs and their descendants.

    Windows can have PM2 wrapper/child node.exe processes. Process owners are
    derived from CIM parent IDs; sockets are read from Get-NetTCPConnection.
    We do not guess ports from env, project names, or unrelated listeners.
    """
    ids = [int(p["pid"]) for p in processes if str(p.get("pid", "")).isdigit() and int(p["pid"]) > 0]
    if not ids:
        return processes
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$proc=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId); "
        "$tcp=@(Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess); "
        "@{processes=$proc;tcp=$tcp} | ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        report = json.loads(run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 18))
        parent = {int(p["ProcessId"]): int(p["ParentProcessId"])
                  for p in report.get("processes", []) if isinstance(p, dict)}
        indexed = {}
        for row in report.get("tcp", []):
            if not isinstance(row, dict):
                continue
            pid, port = int(row["OwningProcess"]), int(row["LocalPort"])
            if 0 < port <= 65535:
                indexed.setdefault(pid, []).append({"port": port,
                    "address": str(row["LocalAddress"])[:45]})
        for proc in processes:
            root = int(proc["pid"]) if str(proc.get("pid", "")).isdigit() else -1
            owned = {root}
            # Bound traversal: avoid chasing malformed or cyclic parent links.
            for _ in range(12):
                children = {pid for pid, ppid in parent.items() if ppid in owned}
                if children.issubset(owned):
                    break
                owned.update(children)
            found = {(item["port"], item["address"])
                     for pid in owned for item in indexed.get(pid, [])}
            proc["ports"] = [{"port": port, "address": ip}
                             for port, ip in sorted(found)[:32]]
    except (ValueError, TypeError, KeyError, OverflowError):
        pass
    return processes


def php_projects():
    """Inventory folders under XAMPP htdocs; never read PHP or .env contents."""
    root = Path(os.environ.get("XAMPP_HOME", r"C:\xampp")) / "htdocs"
    projects = []
    try:
        for folder in sorted(root.iterdir(), key=lambda p: p.name.lower())[:200]:
            if not folder.is_dir() or folder.is_symlink() or folder.name.lower() in ("dashboard", "webalizer", "xampp"):
                continue
            try:
                entries = list(folder.iterdir())[:150]
                php_files = [f for f in entries if f.is_file() and f.suffix.lower() == ".php"]
                if not php_files and not (folder / "composer.json").is_file() and not (folder / "public" / "index.php").is_file():
                    continue
                framework = "Composer" if (folder / "composer.json").is_file() else "PHP"
                if (folder / "artisan").is_file():
                    framework = "Laravel"
                projects.append({"name": folder.name[:120],
                                 "path": str(folder)[:260],
                                 "type": framework, "source": "htdocs"})
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        return []
    # Apache VirtualHosts can use DocumentRoot outside htdocs. Only extract
    # ServerName and DocumentRoot from the local Apache conf (no php or env read).
    conf = Path(os.environ.get("XAMPP_HOME", r"C:\xampp")) / "apache" / "conf" / "extra" / "httpd-vhosts.conf"
    try:
        text = conf.read_text(encoding="utf-8-sig", errors="replace")[:250000]
        blocks = re.findall(r"<VirtualHost\b[^>]*>(.*?)</VirtualHost\s*>", text,
                            flags=re.IGNORECASE | re.DOTALL)[:100]
        for block in blocks:
            name_match = re.search(r"(?im)^\s*ServerName\s+(\S+)", block)
            root_match = re.search(r'(?im)^\s*DocumentRoot\s+(?:"([^"]+)"|(\S+))', block)
            if not root_match:
                continue
            webroot = Path((root_match.group(1) or root_match.group(2)).strip())
            if not webroot.is_dir():
                continue
            domain = name_match.group(1).strip() if name_match else webroot.name
            if any(p["path"].casefold() == str(webroot).casefold() for p in projects):
                continue
            projects.append({"name": domain[:120], "path": str(webroot)[:260],
                             "type": "PHP / Apache", "source": "VirtualHost"})
            if len(projects) >= 100:
                break
    except (OSError, PermissionError):
        pass
    return projects[:100]


def mysql_databases():
    """List databases with read-only monitor credentials, without exposing them.

    A separate account with only minimal metadata privileges should be placed
    in mysql-monitor.cnf inside the protected ProgramData/ServerManager dir.
    No root credentials or passwords are put into argv or agent JSON payload.
    """
    xampp = Path(os.environ.get("XAMPP_HOME", r"C:\xampp"))
    cli = xampp / "mysql" / "bin" / "mysql.exe"
    settings = CONFIG_PATH.parent / "mysql-monitor.cnf"
    if not cli.is_file():
        return {"status": "not_installed", "engine": "MySQL/MariaDB",
                "databases": []}
    if not settings.is_file():
        return {"status": "configuration_required", "engine": "MySQL/MariaDB",
                "databases": []}
    output = run([str(cli), "--defaults-extra-file=" + str(settings),
                  "--protocol=TCP", "--connect-timeout=4",
                  "--batch", "--skip-column-names", "--execute=SHOW DATABASES"], 12)
    if not output:
        return {"status": "unavailable", "engine": "MySQL/MariaDB", "databases": []}
    system = {"information_schema", "performance_schema", "mysql", "sys"}
    names = [line.strip() for line in output.splitlines()
             if line.strip() and line.strip().lower() not in system]
    return {"status": "ok", "engine": "MySQL/MariaDB",
            "databases": [{"name": name[:120]} for name in names[:100]]}


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
              "pm2": windows_pm2_ports(pm2_processes()), "services": windows_services(),
              "php_projects": php_projects(), "mysql": mysql_databases(),
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
