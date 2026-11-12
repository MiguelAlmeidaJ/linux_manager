# Configure an already existing MySQL/MariaDB monitoring account.
# Never changes users, grants, or passwords in the database.
# Run in an elevated PowerShell. Credentials remain on this Windows host.
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Execute o PowerShell como Administrador."
}

$base = Join-Path $env:ProgramData "ServerManager"
$xampp = if ($env:XAMPP_HOME) { $env:XAMPP_HOME } else { "C:\xampp" }
$mysql = Join-Path $xampp "mysql\bin\mysql.exe"
$target = Join-Path $base "mysql-monitor.cnf"
if (-not (Test-Path -LiteralPath $mysql -PathType Leaf)) { throw "Cliente mysql.exe do XAMPP nao encontrado." }
if (-not (Test-Path -LiteralPath $base -PathType Container)) { throw "Agente nao instalado em $base." }
if (Test-Path -LiteralPath $target) {
  Write-Host "O arquivo mysql-monitor.cnf ja existe. Nada foi sobrescrito."
  return
}

$user = Read-Host "Usuario de monitoramento MySQL [lm_monitor]"
if ([string]::IsNullOrWhiteSpace($user)) { $user = "lm_monitor" }
if ($user -notmatch '^[A-Za-z0-9_]{1,32}$') { throw "Nome de usuario MySQL invalido." }
$portText = Read-Host "Porta do MySQL [3306]"
if ([string]::IsNullOrWhiteSpace($portText)) { $portText = "3306" }
if ($portText -notmatch '^[0-9]{1,5}$') { throw "Porta invalida." }
$port = [int]$portText
if ($port -lt 1 -or $port -gt 65535) { throw "Porta invalida." }

$passwordSecure = Read-Host "Senha da conta de monitoramento MySQL" -AsSecureString
$ptr = [IntPtr]::Zero
$password = ""
$temporary = Join-Path $base ("mysql-monitor-" + [guid]::NewGuid().ToString("N") + ".cnf")
try {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passwordSecure)
  $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  if ([string]::IsNullOrEmpty($password)) { throw "Senha nao pode ser vazia." }
  if ($password -match '[\r\n]') { throw "Senha com quebra de linha nao e suportada." }
  function Quote-Option([string]$value) {
    return '"' + $value.Replace('\', '\\').Replace('"', '\"') + '"'
  }
  $lines = @("[client]", "host=127.0.0.1", "port=$port",
             ("user=" + (Quote-Option $user)),
             ("password=" + (Quote-Option $password)))
  [IO.File]::WriteAllText($temporary, ($lines -join [Environment]::NewLine),
                          (New-Object System.Text.UTF8Encoding($false)))
  $password = ""
  & icacls.exe $temporary /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel proteger a credencial local." }

  # mysql client loads password from ACL-protected option file, not argv.
  $output = & $mysql ("--defaults-extra-file=" + $temporary) "--protocol=TCP" "--connect-timeout=5" "--batch" "--skip-column-names" "--execute=SHOW DATABASES" 2>&1
  if ($LASTEXITCODE -ne 0) {
    $raw = [string]::Join(" ", @($output))
    $number = "desconhecido"
    if ($raw -match '(?i)ERROR\s+([0-9]{3,5})') { $number = $Matches[1] }
    throw ("Nao foi possivel listar bancos com a conta existente. Erro MySQL: " + $number +
           ". Verifique senha, porta 3306, host da conta e permissao SHOW DATABASES.")
  }

  Move-Item -LiteralPath $temporary -Destination $target -ErrorAction Stop
  & icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel proteger mysql-monitor.cnf." }
  Write-Host "Conta existente validada. mysql-monitor.cnf criado com acesso restrito."
  Write-Host "Execute: Restart-Service ServerManagerAgent"
} finally {
  $password = ""
  if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
  Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
