"""Server Manager Windows installer launcher (PyInstaller GUI entrypoint).

The per-node ZIP from the admin panel contains this executable (if available),
Install-Agent.ps1, agent code and enrollment.json. No token is embedded in
the EXE. Windows UAC prompts for an administrator before installation.
"""
import ctypes
import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

FILES = ["Install-Agent.ps1", "windows_agent.py", "windows_service.py",
         "windows_metrics.py", "enrollment.json"]


def install():
    folder = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    missing = [filename for filename in FILES if not (folder / filename).is_file()]
    if missing:
        messagebox.showerror("Pacote incompleto",
                             "Extraia o ZIP inteiro antes de executar.\nArquivos ausentes: " + ", ".join(missing))
        return
    # Script is a shipped fixed path, never user-supplied command text.
    script = str(folder / "Install-Agent.ps1")
    parameters = '-NoProfile -ExecutionPolicy Bypass -File "' + script.replace('"', '') + '"'
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                                                  parameters, str(folder), 1)
    if result <= 32:
        messagebox.showerror("Instalação não iniciada",
                             "A execução elevada foi cancelada ou bloqueada pelo Windows.")
    else:
        messagebox.showinfo("Instalação iniciada",
                            "Conclua as etapas no PowerShell administrativo que foi aberto.\n"
                            "Depois verifique o serviço ServerManagerAgent em services.msc.")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Este aplicativo executa somente no Windows.")
    root = tk.Tk()
    root.title("Server Manager - Instalador Windows")
    root.geometry("460x280")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=28)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Server Manager", font=("Segoe UI", 20, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Agente de monitoramento para Windows Server",
              font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 17))
    ttk.Label(frame, text="O serviço será configurado para iniciar automaticamente\n"
              "com o Windows, sem depender de uma sessão aberta.",
              justify="left").pack(anchor="w")
    ttk.Button(frame, text="Instalar ou atualizar agente", command=install).pack(fill="x", pady=(20, 0))
    ttk.Label(frame, text="Requer permissão de administrador. Revise os scripts antes da instalação.",
              foreground="#667085", font=("Segoe UI", 9)).pack(anchor="w", pady=(15, 0))
    root.mainloop()
