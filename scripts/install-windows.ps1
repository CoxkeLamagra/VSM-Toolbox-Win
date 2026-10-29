#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$InstallDirectory = "$env:ProgramFiles\VSMToolbox",
    [string]$DataDirectory = "$env:ProgramData\VSMToolbox",
    [switch]$AllowPrivateNetwork
)
$ErrorActionPreference = 'Stop'
$Source = Split-Path -Parent $PSScriptRoot
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}
$Python = (Get-Command $Python -ErrorAction Stop).Source
Invoke-Checked $Python @('-c', 'import sys; assert sys.version_info >= (3,11); assert sys.prefix == sys.base_prefix, "Use a machine-wide Python installation, not a virtual environment"')
$InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
if ($InstallDirectory.StartsWith('\\') -or $DataDirectory.StartsWith('\\')) { throw 'Use local disk paths, not network shares.' }
if ($InstallDirectory.TrimEnd('\') -eq $DataDirectory.TrimEnd('\') -or $DataDirectory.StartsWith($InstallDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $InstallDirectory.StartsWith($DataDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Application and data directories must be separate, non-overlapping paths.' }
if (Test-Path $InstallDirectory) { throw 'Install directory already exists. Use the documented maintenance procedure; existing files were not changed.' }
if (Get-Service VSMToolbox -ErrorAction SilentlyContinue) { throw 'VSMToolbox service already exists.' }
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8080 is already in use. Stop the conflicting application before installing.' }
$manifest = Get-Content (Join-Path $Source 'update-manifest.json') -Raw | ConvertFrom-Json
# Validate all payload paths through the application validator before copying.
Push-Location $Source
try {
    Invoke-Checked $Python @('-c', 'from vsm.runtime import FILES; import pathlib; assert all((pathlib.Path(f).is_file()) for f in FILES)')
} finally { Pop-Location }
Invoke-Checked $Python @('-m', 'pip', 'install', 'pywin32==311')
$postInstall = & $Python -c 'import sysconfig; print(sysconfig.get_path("scripts"))'
Invoke-Checked $Python @((Join-Path $postInstall 'pywin32_postinstall.py'), '-install')
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
# LocalService can change local business data and web-update releases, but not installed code.
Invoke-Checked 'icacls.exe' @($DataDirectory, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-19:(OI)(CI)M')
Invoke-Checked 'icacls.exe' @($InstallDirectory, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-19:(OI)(CI)RX')
foreach ($name in @($manifest.files) + @('windows_service.py', 'update-manifest.json')) {
    $target = Join-Path $InstallDirectory $name
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $Source $name) -Destination $target -Force
}
@{data_directory=$DataDirectory;python_executable=$Python} | ConvertTo-Json | Set-Content (Join-Path $InstallDirectory 'service.json') -Encoding UTF8
$serviceScript = Join-Path $InstallDirectory 'windows_service.py'
Invoke-Checked $Python @($serviceScript, '--startup', 'auto', '--username', 'NT AUTHORITY\LocalService', 'install')
# pythonservice.exe resolves the service module independently of the interactive working directory.
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\VSMToolbox\PythonClass' -Name '(default)' -Value 'windows_service.VSMService'
New-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\VSMToolbox\PythonPath' -Force | Out-Null
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\VSMToolbox\PythonPath' -Name '(default)' -Value $InstallDirectory
Invoke-Checked 'sc.exe' @('failure', 'VSMToolbox', 'reset=', '86400', 'actions=', 'restart/5000/restart/15000/restart/60000')
if ($AllowPrivateNetwork) {
    New-NetFirewallRule -Name 'VSMToolbox-HTTP' -DisplayName 'VSM Toolbox HTTP 8080' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
}
Start-Service VSMToolbox
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8080/api/health' -TimeoutSec 2
        if ((Get-Service VSMToolbox).Status -eq 'Running' -and $health.status -eq 'ok' -and $health.update_protocol -eq 2) { $ready=$true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Stop-Service VSMToolbox -ErrorAction SilentlyContinue; throw "Health check failed. Inspect $DataDirectory\service.log and application.log. Installation retained for diagnosis." }
Write-Host "Installed VSMToolbox. Open http://localhost:8080 and create your first Admin account. Data: $DataDirectory"
