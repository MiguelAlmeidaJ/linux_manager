# Server Manager Windows Agent installer.
# Run as Administrator. Review this file before execution.
# Uses official winget Python package if Python 3.11+ is missing.
# Native Windows service uses pywin32, auto-started at boot.
#Requires -Version 5.1
param([switch]$NoPythonInstall)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Execute o PowerShell como Administrador."
}
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Join-Path $source "enrollment.json"
if (-not (Test-Path -LiteralPath $manifest)) { throw "enrollment.json nao encontrado. Extraia o ZIP completo." }
$data = Get-Content -Raw -LiteralPath $manifest -Encoding UTF8 | ConvertFrom-Json
if ($data.server_url -notmatch '^https://') { throw "O controlador deve usar HTTPS." }
if (-not $data.token -or -not $data.node_id) { throw "Credenciais do agente ausentes." }

$python = $null
function Find-Python {
  foreach ($command in @("py", "python")) {
    $candidate = Get-Command $command -ErrorAction SilentlyContinue
    if (-not $candidate) { continue }
    try {
      $args = if ($command -eq "py") { @("-3", "-c", "import sys; print(sys.executable); print(sys.version_info.major, sys.version_info.minor)") } else { @("-c", "import sys; print(sys.executable); print(sys.version_info.major, sys.version_info.minor)") }
      $out = @(& $command @args 2>$null)
      if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2) {
        $version = $out[1].Trim().Split(" ")
        if ([int]$version[0] -ge 3 -and [int]$version[1] -ge 11) { return $out[0].Trim() }
      }
    } catch {}
  }
  return $null
}
$python = Find-Python
if (-not $python) {
  if ($NoPythonInstall) { throw "Python 3.11+ nao encontrado." }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ nao encontrado e winget indisponivel. Instale Python do python.org, depois execute novamente."
  }
  Write-Host "Instalando Python 3.12 pelo Windows Package Manager..."
  & winget install --id Python.Python.3.12 --exact --source winget --scope machine --silent --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel instalar Python. Instale Python 3.11+ manualmente." }
  $python = Find-Python
  if (-not $python) {
    $paths = @("$env:ProgramFiles\Python312\python.exe", "$env:LocalAppData\Programs\Python\Python312\python.exe")
    $python = $paths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  }
  if (-not $python) { throw "Python instalado mas nao localizado. Abra novo PowerShell e execute novamente." }
}
Write-Host "Python: $python"
& $python -m pip --version
if ($LASTEXITCODE -ne 0) { & $python -m ensurepip --upgrade }
& $python -m pip install --disable-pip-version-check "pywin32>=306,<400"
if ($LASTEXITCODE -ne 0) { throw "Falha instalando pywin32." }

$target = Join-Path $env:ProgramData "ServerManager"
New-Item -ItemType Directory -Path $target -Force | Out-Null
# Agent runs as LocalSystem. Only Administrators and SYSTEM can read secrets.
& icacls $target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Falha configurando ACL do agente." }

$service = Get-Service -Name "ServerManagerAgent" -ErrorAction SilentlyContinue
if ($service) {
  & $python (Join-Path $target "windows_service.py") stop 2>$null
  Start-Sleep -Seconds 2
}
Copy-Item -LiteralPath (Join-Path $source "windows_agent.py") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $source "windows_service.py") -Destination $target -Force
# PM2 runs per user. When installing from the PM2 owner session, this
# points to that user's daemon; otherwise edit pm2_home after install.
$pm2Home = if ($env:PM2_HOME) { $env:PM2_HOME } else { Join-Path $env:USERPROFILE ".pm2" }
$detectedPM2 = Get-Command pm2.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
$pm2Command = if ($detectedPM2) { [string]$detectedPM2.Source } else { "" }
$config = @{
  server_url = [string]$data.server_url
  node_id = [int]$data.node_id
  token = [string]$data.token
  pm2_home = [string]$pm2Home
  pm2_command = [string]$pm2Command
} | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $target "agent.json"), $config, [Text.UTF8Encoding]::new($false))
& icacls (Join-Path $target "agent.json") /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Falha protegendo o token." }

if ($service) {
  & $python (Join-Path $target "windows_service.py") remove
  if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel atualizar o servico existente." }
}
& $python (Join-Path $target "windows_service.py") --startup auto install
if ($LASTEXITCODE -ne 0) { throw "Falha registrando servico Windows." }
& $python (Join-Path $target "windows_service.py") start
if ($LASTEXITCODE -ne 0) { throw "Falha iniciando servico Windows." }

# Automatic recovery if the service exits unexpectedly.
& sc.exe failure ServerManagerAgent reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Warning "Nao foi possivel configurar recuperacao automatica de falhas." }

Write-Host ""
Write-Host "Server Manager Agent instalado e iniciado como servico automatico."
Write-Host "PM2_HOME configurado: $pm2Home"
Write-Host "PM2 CLI configurado: $pm2Command"
Write-Host "Verifique em services.msc > Server Manager Agent."
Write-Warning "APAGUE o ZIP e a pasta extraida depois da instalacao: eles contem o token de acesso."
Write-Warning "Se o PM2 estiver em outra conta Windows, ajuste pm2_home e pm2_command em $target\agent.json e reinicie o servico."
