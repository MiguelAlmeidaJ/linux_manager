"""Read-only Windows resource telemetry, stdlib-only.

CPU is an instantaneous Windows WMI load estimate; per-process percentages
come from Windows formatted counters, not lifetime Get-Process CPU time.
"""
import json
import subprocess

SCRIPT = r"""
$ErrorActionPreference='Stop'
$os=Get-CimInstance Win32_OperatingSystem
$cpus=@(Get-CimInstance Win32_Processor)
$cpu=0
if($cpus.Count -gt 0){$cpu=[math]::Round((($cpus|Measure-Object -Property LoadPercentage -Average).Average),1)}
$total=[double]$os.TotalVisibleMemorySize*1024
$free=[double]$os.FreePhysicalMemory*1024
$cores=[math]::Max(1,[int]$os.NumberOfLogicalProcessors)
if($cores -le 1){$cores=[math]::Max(1,($cpus|Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)}
$processes=@(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
 Where-Object {$_.IDProcess -gt 0 -and $_.Name -notin @('_Total','Idle')} |
 Select-Object -First 400 Name,IDProcess,PercentProcessorTime,WorkingSetPrivate)
$disks=@(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
 Select-Object DeviceID,Size,FreeSpace)
@{cpu_percent=$cpu;memory_total=$total;memory_used=($total-$free);
cores=$cores;processes=$processes;disks=$disks} | ConvertTo-Json -Depth 5 -Compress
"""


def collect():
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                 "-Command", SCRIPT], capture_output=True,
                                text=True, timeout=18,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return {}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            return {}
        cores = max(1, int(payload.get("cores") or 1))
        processes = payload.get("processes") or []
        if isinstance(processes, dict):
            processes = [processes]
        top = []
        for item in processes:
            if not isinstance(item, dict):
                continue
            try:
                top.append({"name": str(item.get("Name") or "")[:90],
                            "pid": int(item.get("IDProcess") or 0),
                            "cpu_percent": round(max(0, min(100,
                                float(item.get("PercentProcessorTime") or 0)/cores)), 1),
                            "memory_bytes": max(0, int(item.get("WorkingSetPrivate") or 0))})
            except (ValueError, TypeError):
                continue
        top.sort(key=lambda p: (p["memory_bytes"], p["cpu_percent"]), reverse=True)
        disks = payload.get("disks") or []
        if isinstance(disks, dict):
            disks = [disks]
        return {"cpu_percent": round(float(payload.get("cpu_percent") or 0), 1),
                "memory_percent": round(100 * float(payload.get("memory_used") or 0) /
                                        max(1, float(payload.get("memory_total") or 1)), 1),
                "memory_total": int(payload.get("memory_total") or 0),
                "memory_used": int(payload.get("memory_used") or 0),
                "top_processes": top[:30],
                "disks": [{"name": str(p.get("DeviceID") or "")[:10],
                           "size": int(p.get("Size") or 0),
                           "free": int(p.get("FreeSpace") or 0)}
                          for p in disks[:12] if isinstance(p, dict)]}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, OverflowError):
        return {}
