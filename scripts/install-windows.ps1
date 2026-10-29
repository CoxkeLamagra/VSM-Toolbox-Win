#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$Python,
    [string]$PythonInstallerPath,
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
function Install-PythonRuntime([string]$TargetDirectory, [string]$InstallerPath) {
    $version = '3.13.14'
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $previousProtocols = [Net.ServicePointManager]::SecurityProtocol
    try {
        if ($InstallerPath) {
            $installer = (Resolve-Path -LiteralPath $InstallerPath -ErrorAction Stop).ProviderPath
        } else {
            $installer = Join-Path $temporary "python-$version-amd64.exe"
            [Net.ServicePointManager]::SecurityProtocol = $previousProtocols -bor [Net.SecurityProtocolType]::Tls12
            Write-Host "Downloading Python $version from python.org..."
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$version/python-$version-amd64.exe" -OutFile $installer -UseBasicParsing
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O=Python Software Foundation(?:,|$)') {
            throw 'Python installer signature is invalid or its publisher is not the Python Software Foundation. Nothing was executed.'
        }
        $process = Start-Process -FilePath $installer -ArgumentList @(
            '/quiet', 'InstallAllUsers=1', ('TargetDir="' + $TargetDirectory + '"'),
            'Include_pip=1', 'Include_launcher=0', 'InstallLauncherAllUsers=0',
            'Include_test=0', 'Include_doc=0', 'PrependPath=0', 'AssociateFiles=0',
            '/log', ('"' + (Join-Path $env:TEMP 'VSMToolbox-Python-install.log') + '"')
        ) -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            throw 'Python requires a Windows restart. Restart Windows, then rerun this installer. See %TEMP%\VSMToolbox-Python-install.log.'
        }
        if ($process.ExitCode -ne 0) {
            throw "Python installation failed (exit $($process.ExitCode)). See %TEMP%\VSMToolbox-Python-install.log."
        }
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $previousProtocols
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
function Get-MachinePython {
    $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        foreach ($version in @('3.13', '3.12', '3.11')) {
            $key = $registry.OpenSubKey("SOFTWARE\Python\PythonCore\$version\InstallPath")
            if ($null -eq $key) { continue }
            try {
                $candidate = $key.GetValue('ExecutablePath')
                if (-not $candidate -and $key.GetValue('')) { $candidate = Join-Path $key.GetValue('') 'python.exe' }
                if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
            } finally { $key.Dispose() }
        }
    } finally { $registry.Dispose() }
}
function Resolve-ServicePython([string]$Executable, [string]$InstallerPath) {
    if ($Executable -and $InstallerPath) { throw 'Choose -Python or -PythonInstallerPath, not both.' }
    if ($Executable) {
        $resolved = (Get-Command $Executable -CommandType Application -ErrorAction Stop).Source
    } else {
        $runtimeDirectory = Join-Path $env:ProgramFiles 'VSMToolbox-Python'
        $resolved = Join-Path $runtimeDirectory 'python.exe'
        if (-not (Test-Path -LiteralPath $resolved)) {
            $existing = Get-MachinePython
            if ($existing) {
                $resolved = $existing
            } else {
                Install-PythonRuntime $runtimeDirectory $InstallerPath
            }
        }
    }
    Invoke-Checked $resolved @('-c', 'import sys,struct,os,pathlib; assert (3,11) <= sys.version_info[:2] < (3,14), "Python 3.11-3.13 is required"; assert struct.calcsize("P") == 8, "64-bit Python is required"; assert sys.prefix == sys.base_prefix, "Use machine-wide Python, not a virtual environment"; assert "windowsapps" not in sys.executable.lower() and not pathlib.Path(sys.executable).is_relative_to(pathlib.Path(os.environ["USERPROFILE"])), "Use machine-wide Python outside your user profile"') | Out-Host
    Invoke-Checked $resolved @('-m', 'ensurepip', '--upgrade') | Out-Host
    return $resolved
}
$InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
if ($InstallDirectory.StartsWith('\\') -or $DataDirectory.StartsWith('\\')) { throw 'Use local disk paths, not network shares.' }
if ($InstallDirectory.TrimEnd('\') -eq $DataDirectory.TrimEnd('\') -or $DataDirectory.StartsWith($InstallDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $InstallDirectory.StartsWith($DataDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Application and data directories must be separate, non-overlapping paths.' }
if (Test-Path $InstallDirectory) { throw 'Install directory already exists. Use the documented maintenance procedure; existing files were not changed.' }
if (Get-Service VSMToolbox -ErrorAction SilentlyContinue) { throw 'VSMToolbox service already exists.' }
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8080 is already in use. Stop the conflicting application before installing.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw 'This installer supports x64 Windows; ARM64 is not supported.' }
$Python = Resolve-ServicePython $Python $PythonInstallerPath
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
