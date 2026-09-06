[CmdletBinding()]
param(
    [string]$Zig = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Source = Join-Path $Root 'tools\native-stream\cr_arm_entity_stream.c'
$Output = Join-Path $Root 'runtime\cr-arm-entity-stream-x86_64'

if (-not $Zig) {
    $Zig = (Get-Command zig -ErrorAction SilentlyContinue).Source
}
if (-not $Zig -or -not (Test-Path -LiteralPath $Zig)) {
    throw 'Zig was not found. Install Zig and ensure zig.exe is on PATH, then rerun this script.'
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Output) -Force | Out-Null
$cache = Join-Path $Root 'runtime\zig-cache'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$env:ZIG_GLOBAL_CACHE_DIR = $cache
# A static musl ELF runs on the emulator's Linux kernel without requiring an
# Android sysroot or dynamic linker from the host SDK.
& $Zig cc '-target' 'x86_64-linux-musl' '-O3' '-std=c17' '-static' '-s' '-o' $Output $Source
if ($LASTEXITCODE -ne 0) { throw 'Zig failed to build the native helper.' }
Write-Host "Built $Output" -ForegroundColor Green
