[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Script = Join-Path $PSScriptRoot 'deploy\emote_blacklist_ui.py'
$Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    throw 'Python was not found on PATH.'
}

Start-Process -FilePath $Python -ArgumentList @($Script) -WorkingDirectory $PSScriptRoot
