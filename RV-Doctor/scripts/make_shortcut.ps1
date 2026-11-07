$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root '启动体检台.bat'
$ico  = Join-Path $root 'assets\rv-doctor.ico'

if (-not (Test-Path $bat)) { throw "target not found: $bat" }
if (-not (Test-Path $ico)) { throw "icon not found: $ico" }

function New-Lnk($path) {
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($path)
  $s.TargetPath       = $bat
  $s.WorkingDirectory = $root
  $s.IconLocation     = "$ico,0"
  $s.WindowStyle      = 7   # 启动后控制台最小化到任务栏
  $s.Description      = 'RV-Doctor 一键启动（RISC-V 代码性能诊断与自动优化平台）'
  $s.Save()
}

$paths = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'RV-Doctor体检台.lnk')
)
foreach ($p in $paths) { New-Lnk $p }

$lines = @()
foreach ($p in $paths) {
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($p)
  $lines += "LNK: $p"
  $lines += "  Target : $($s.TargetPath)"
  $lines += "  WorkDir: $($s.WorkingDirectory)"
  $lines += "  Icon   : $($s.IconLocation)"
  $lines += "  Style  : $($s.WindowStyle)  FileExists: $(Test-Path $p)"
}
$logDir = Join-Path $root 'workdir\shortcuts'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $logDir 'lnk_check.txt'), ($lines -join "`r`n"), [Text.Encoding]::UTF8)
Write-Output 'shortcuts created'
