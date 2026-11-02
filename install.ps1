# Freezeframe installer for Windows. In PowerShell:
#
#   irm https://raw.githubusercontent.com/atharv146/freezeframe/main/install.ps1 | iex
#
# Installs Freezeframe into its own Python environment, adds the `freezeframe`
# command to your PATH, and starts recording in the background (a Task
# Scheduler task that runs at sign-in). No administrator rights needed.
# Undo with:  freezeframe uninstall
#
# Options (environment variables):
#   FREEZEFRAME_SOURCE=...        install from this pip source instead of PyPI/GitHub
#   FREEZEFRAME_NO_AUTOSTART=1    install only; don't start background recording

$ErrorActionPreference = 'Stop'
$GithubZip = 'https://github.com/atharv146/freezeframe/archive/refs/heads/main.zip'
$AppDir = Join-Path $env:LOCALAPPDATA 'Freezeframe\app'

function Fail($message) {
    Write-Host "Freezeframe install failed: $message" -ForegroundColor Red
    exit 1
}

# Find a real Python 3.10+ (the Microsoft Store "python" stub doesn't count).
$python = $null
foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
    $exe = $candidate[0]
    $prefix = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @prefix -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}
if (-not $python) {
    Fail 'Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.'
}
$exe = $python[0]
$prefix = @($python | Select-Object -Skip 1)

Write-Host "Installing Freezeframe with $(& $exe @prefix --version)..."
New-Item -ItemType Directory -Force -Path (Split-Path $AppDir) | Out-Null
& $exe @prefix -m venv $AppDir
if ($LASTEXITCODE -ne 0) { Fail "couldn't create a Python environment in $AppDir" }

$vpy = Join-Path $AppDir 'Scripts\python.exe'
& $vpy -m pip install --quiet --disable-pip-version-check --upgrade pip
# Includes the tray icon (pystray + Pillow).
if ($env:FREEZEFRAME_SOURCE) {
    & $vpy -m pip install --quiet --disable-pip-version-check --upgrade "$($env:FREEZEFRAME_SOURCE)[menubar]"
} else {
    & $vpy -m pip install --quiet --disable-pip-version-check --upgrade 'freezeframe[menubar]' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $vpy -m pip install --quiet --disable-pip-version-check --upgrade "freezeframe[menubar] @ $GithubZip"
    }
}
if ($LASTEXITCODE -ne 0) { Fail 'pip could not install Freezeframe' }

$scripts = Join-Path $AppDir 'Scripts'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $scripts)) {
    [Environment]::SetEnvironmentVariable('Path', (@($userPath, $scripts) | Where-Object { $_ }) -join ';', 'User')
    Write-Host 'Added Freezeframe to your PATH (open a new terminal for it to take effect).'
}
$env:Path = "$env:Path;$scripts"

$ff = Join-Path $scripts 'freezeframe.exe'
if ($env:FREEZEFRAME_NO_AUTOSTART -ne '1') {
    & $ff install
    if ($LASTEXITCODE -ne 0) { Fail 'could not start background recording' }
}

Write-Host ''
Write-Host "$(& $ff --version) is installed."
Write-Host "  freezeframe now     what's running right now, explained"
Write-Host '  freezeframe why     explain the latest freeze'
Write-Host '  freezeframe open    open the timeline dashboard'
Write-Host '  freezeframe open --demo    explore a demo recording right now'
