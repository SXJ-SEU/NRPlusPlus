[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectRoot 'runtime'
$PythonBin = (Get-Command python -ErrorAction SilentlyContinue).Source

if (-not $PythonBin) {
    throw 'Python was not found on PATH.'
}

Write-Host "Using Python: $PythonBin"
& $PythonBin -m pip install --disable-pip-version-check pygame
if ($LASTEXITCODE -ne 0) { throw 'Pygame installation failed.' }

$Zig = (Get-Command zig -ErrorAction SilentlyContinue).Source
if (-not $Zig) {
    $portableZig = Join-Path $RuntimeDir 'zig\zig.exe'
    if (Test-Path -LiteralPath $portableZig) { $Zig = $portableZig }
}
if (-not $Zig) {
    throw 'Zig was not found. Install Zig 0.15.2 or place zig.exe at runtime\zig\zig.exe.'
}

& (Join-Path $ProjectRoot 'tools\build_native_helper.ps1') -Zig $Zig
if ($LASTEXITCODE -ne 0) { throw 'Native helper build failed.' }

Write-Host 'Setup completed.' -ForegroundColor Green
Write-Host 'Run: python tools\capture_native_entity_stream.py --serial localhost:5557 --package nullsroyale.rel.free'
