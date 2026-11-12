"""Native Windows service; requires pywin32 installed for the selected Python.

Install/remove with PowerShell Install-Agent.ps1. Runs as LocalSystem,
and PM2 visibility can be configured via PM2_HOME in protected agent.json.
"""
import os
import sys
import threading
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

BASE = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ServerManager"
sys.path.insert(0, str(BASE))
import windows_agent


class ServerManagerAgent(win32serviceutil.ServiceFramework):
    _svc_name_ = "ServerManagerAgent"
    _svc_display_name_ = "Server Manager Agent"
    _svc_description_ = "Reports Windows PM2 and XAMPP status to Server Manager using HTTPS."

    def __init__(self, args):
        super().__init__(args)
        self.halt = threading.Event()
        self.stop_handle = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.halt.set()
        win32event.SetEvent(self.stop_handle)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("Server Manager Agent starting")
        windows_agent.run_forever(self.halt)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(ServerManagerAgent)
