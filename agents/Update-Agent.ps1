# Update an installed Server Manager Windows agent without changing enrollment token.
# Run elevated in the directory extracted from a ZIP downloaded in the panel.
#Requires -Version 5.1
param([switch]$SkipMySQL)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Execute o PowerShell como Administrador."
}
$target = Join-Path $env:ProgramData "ServerManager"
$config = Join-Path $target "agent.json"
if (-not (Test-Path -LiteralPath $config)) {
  throw "Agente nao instalado. Use o instalador completo, nao o atualizador."
}
$service = Get-Service -Name ServerManagerAgent -ErrorAction SilentlyContinue
if (-not $service) { throw "Servico ServerManagerAgent nao encontrado." }
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @("windows_agent.py", "windows_metrics.py", "windows_service.py")
foreach ($name in $files) {
  if (-not (Test-Path -LiteralPath (Join-Path $source $name))) {
    throw "Pacote incompleto: $name"
  }
}
$backups = @()
try {
  Stop-Service -Name ServerManagerAgent -ErrorAction Stop
  $service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
  foreach ($name in $files) {
    $dest = Join-Path $target $name
    $backup = Join-Path $target ($name + ".pre-update")
    if (Test-Path -LiteralPath $dest) {
      Copy-Item -LiteralPath $dest -Destination $backup -Force
      $backups += $name
    }
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination $dest -Force
  }
  Start-Service -Name ServerManagerAgent -ErrorAction Stop
  (Get-Service -Name ServerManagerAgent).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
  Write-Host "Agente atualizado. Serviço iniciado e configuracao preservada."
  foreach ($name in $backups) {
    Remove-Item -LiteralPath (Join-Path $target ($name + ".pre-update")) -Force -ErrorAction SilentlyContinue
  }
} catch {
  Write-Warning "Falha na atualizacao. Tentando restaurar os scripts anteriores."
  foreach ($name in $backups) {
    $backup = Join-Path $target ($name + ".pre-update")
    if (Test-Path -LiteralPath $backup) {
      Copy-Item -LiteralPath $backup -Destination (Join-Path $target $name) -Force
    }
  }
  try { Start-Service -Name ServerManagerAgent -ErrorAction Stop } catch {}
  throw
}
if (-not $SkipMySQL) {
  $mysqlScript = Join-Path $source "Configure-MySQL.ps1"
  if (Test-Path -LiteralPath $mysqlScript) {
    & $mysqlScript
  }
}
Write-Host "Token, PM2_HOME e configuracoes existentes nao foram alterados."
