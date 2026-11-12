# Optional local MySQL/MariaDB monitoring setup.
# Run as Administrator on the Windows host. Secrets stay on that host.
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$base = Join-Path $env:ProgramData "ServerManager"
$xampp = if ($env:XAMPP_HOME) { $env:XAMPP_HOME } else { "C:\xampp" }
$mysql = Join-Path $xampp "mysql\bin\mysql.exe"
$monitorFile = Join-Path $base "mysql-monitor.cnf"
if (-not (Test-Path -LiteralPath $mysql -PathType Leaf)) {
  Write-Host "XAMPP MySQL nao encontrado; monitoramento ignorado."
  return
}
if (Test-Path -LiteralPath $monitorFile) {
  Write-Host "Configuracao MySQL existente preservada."
  return
}
$choice = Read-Host "O usuario lm_monitor ja existe no banco? [S/n]"
if ($choice -notmatch '^[nN]') {
  $existing = Join-Path $PSScriptRoot "Configure-Existing-MySQL.ps1"
  if (-not (Test-Path -LiteralPath $existing -PathType Leaf)) {
    throw "Configure-Existing-MySQL.ps1 nao encontrado no pacote. Baixe o ZIP atualizado."
  }
  & $existing
  return
}
$choice = Read-Host "Criar novo usuario MySQL lm_monitor usando credenciais administrativas? [s/N]"
if ($choice -notmatch '^[sS]') { Write-Host "Configuracao MySQL ignorada."; return }
if ($choice -match '^[nN]') { Write-Host "Monitoramento ignorado."; return }
$adminUser = Read-Host "Usuario administrativo do MySQL [root]"
if ([string]::IsNullOrWhiteSpace($adminUser)) { $adminUser = "root" }
if ($adminUser -notmatch '^[A-Za-z0-9_]{1,32}$') {
  Write-Warning "Nome de usuario invalido."; return
}
# Identify port from the [mysqld] section in XAMPP my.ini.
$port = 3306
$ini = Join-Path $xampp "mysql\bin\my.ini"
if (Test-Path -LiteralPath $ini) {
  $section = ""
  foreach ($line in (Get-Content -LiteralPath $ini -ErrorAction SilentlyContinue)) {
    $trimmed = $line.Trim()
    if ($trimmed -match '^\[([^\]]+)\]') {
      $section = $Matches[1].ToLowerInvariant()
      continue
    }
    if ($section -eq "mysqld" -and $trimmed -match '^port\s*=\s*([0-9]{1,5})') {
      $candidate = [int]$Matches[1]
      if ($candidate -ge 1 -and $candidate -le 65535) { $port = $candidate }
      break
    }
  }
}
Write-Host ("Porta MySQL configurada: " + $port)
$portAnswer = Read-Host ("Porta TCP do MySQL [" + $port + "]")
if (-not [string]::IsNullOrWhiteSpace($portAnswer)) {
  if ($portAnswer -notmatch '^[0-9]{1,5}$') {
    Write-Warning "Porta invalida."; return
  }
  $port = [int]$portAnswer
  if ($port -lt 1 -or $port -gt 65535) {
    Write-Warning "Porta fora do intervalo permitido."; return
  }
}
# Test-NetConnection is available on supported Windows Server releases.
if (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue)) {
  Write-Warning ("MySQL nao responde em 127.0.0.1:" + $port)
  Write-Warning "Verifique o servico do XAMPP e a porta configurada."
  return
}
$adminSecure = Read-Host "Senha do usuario MySQL (Enter se vazia)" -AsSecureString
$ptr = [IntPtr]::Zero
$adminPassword = ""
try {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminSecure)
  $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
  if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Quote-Option([string]$value) {
  if ($value -match '[\r\n]') { throw "Quebras de linha nao suportadas." }
  return '"' + $value.Replace('\', '\\').Replace('"', '\"') + '"'
}
function Protect-File([string]$path) {
  & icacls.exe $path /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Falha protegendo arquivo." }
}
function Invoke-LocalMySQL([string]$config, [string]$sql) {
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo.FileName = $mysql
  $proc.StartInfo.Arguments = ('--defaults-extra-file="' + $config + '" --protocol=TCP --connect-timeout=5 --batch --skip-column-names')
  $proc.StartInfo.UseShellExecute = $false
  $proc.StartInfo.CreateNoWindow = $true
  $proc.StartInfo.RedirectStandardInput = $true
  $proc.StartInfo.RedirectStandardOutput = $true
  $proc.StartInfo.RedirectStandardError = $true
  if (-not $proc.Start()) { throw "Cliente MySQL nao iniciado." }
  try {
    $proc.StandardInput.WriteLine($sql)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    if (-not $proc.WaitForExit(15000)) {
      try { $proc.Kill() } catch {}
      throw "Timeout MySQL."
    }
    if ($proc.ExitCode -ne 0) {
      # Never print raw stderr: some MySQL messages may contain sensitive values.
      $errorNumber = "desconhecido"
      if ($stderr -match '(?i)(?:ERROR|ERRORCODE)\s+([0-9]{3,5})') { $errorNumber = $Matches[1] }
      $reason = switch ($errorNumber) {
        "1045" { "Acesso negado: verifique usuario, senha e origem localhost/127.0.0.1." }
        "1044" { "Conta conectou, mas nao possui privilegios suficientes." }
        "2002" { "Falha de conexao: servidor indisponivel ou protocolo incorreto." }
        "2003" { "Falha de conexao TCP: porta ou endereco incorreto." }
        "1064" { "Erro SQL: versao/sintaxe incompativel." }
        default { "Consulte o log local do MySQL; confirme o metodo de autenticacao." }
      }
      throw ("MySQL exit=" + $proc.ExitCode + "; erro MySQL=" + $errorNumber + ". " + $reason)
    }
    return $stdout.Trim()
  } finally { $proc.Dispose() }
}
$temporary = Join-Path $base ("mysql-admin-" + [Guid]::NewGuid().ToString("N") + ".cnf")
$testConfig = Join-Path $base ("mysql-monitor-" + [Guid]::NewGuid().ToString("N") + ".cnf")
$password = ""
$stage = "validar conexao administrativa"
try {
  $lines = @("[client]", "host=127.0.0.1", "port=$port", "user=" + (Quote-Option $adminUser), "password=" + (Quote-Option $adminPassword))
  [IO.File]::WriteAllText($temporary, ($lines -join [Environment]::NewLine), $utf8)
  $adminPassword = ""
  Protect-File $temporary
  [void](Invoke-LocalMySQL $temporary "SELECT 1;")
  $bytes = New-Object byte[] 24
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  $password = ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
  # MariaDB/MySQL can distinguish localhost and 127.0.0.1 for TCP.
  $query = "CREATE USER IF NOT EXISTS 'lm_monitor'@'localhost' IDENTIFIED BY '$password'; " +
           "ALTER USER 'lm_monitor'@'localhost' IDENTIFIED BY '$password'; " +
           "GRANT SHOW DATABASES ON *.* TO 'lm_monitor'@'localhost'; " +
           "CREATE USER IF NOT EXISTS 'lm_monitor'@'127.0.0.1' IDENTIFIED BY '$password'; " +
           "ALTER USER 'lm_monitor'@'127.0.0.1' IDENTIFIED BY '$password'; " +
           "GRANT SHOW DATABASES ON *.* TO 'lm_monitor'@'127.0.0.1';"
  $stage = "criar e conceder privilegios ao lm_monitor"
  [void](Invoke-LocalMySQL $temporary $query)
  $lines = @("[client]", "host=127.0.0.1", "port=$port", "user=lm_monitor", "password=$password")
  [IO.File]::WriteAllText($testConfig, ($lines -join [Environment]::NewLine), $utf8)
  Protect-File $testConfig
  $stage = "validar conta de monitoramento"
  [void](Invoke-LocalMySQL $testConfig "SHOW DATABASES;")
  Move-Item -LiteralPath $testConfig -Destination $monitorFile -ErrorAction Stop
  Protect-File $monitorFile
  Write-Host "Conta lm_monitor e arquivo mysql-monitor.cnf configurados e validados."
} catch {
  Write-Warning ("MySQL nao configurado na etapa [" + $stage + "]: " + $_.Exception.Message)
  Write-Warning "O agente segue funcionando. Reexecute a configuracao posteriormente."
} finally {
  $adminPassword = ""
  $password = ""
  Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $testConfig -Force -ErrorAction SilentlyContinue
}
