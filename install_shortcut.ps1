# Creates "Bingo Helper.lnk" on your Desktop.
# The shortcut starts start_silent.vbs with the global hotkey Ctrl+Alt+B.
# Pressing that hotkey while the game is focused starts the helper WITHOUT
# taking focus, so the game does not pause.
#
# Run once:
#   powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1

$ErrorActionPreference = 'Stop'

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $base 'start_silent.vbs'
if (-not (Test-Path $target)) {
    throw "Cannot find $target"
}

$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'Bingo Helper.lnk'

$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$lnk.Arguments = '"' + $target + '"'
$lnk.WorkingDirectory = $base
$lnk.Hotkey = 'CTRL+ALT+B'
$lnk.WindowStyle = 7
$lnk.Description = 'Start the bingo helper silently'

$lnk.Save()

Write-Host "Created: $lnkPath"
Write-Host "Global hotkey: Ctrl+Alt+B"
Write-Host "It works while the game is focused; the helper does not steal focus."
