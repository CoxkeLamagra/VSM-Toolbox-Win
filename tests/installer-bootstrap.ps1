# Run with PowerShell; no elevated privileges, downloads, or installed Python needed.
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/install-windows.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
# Load only helper definitions, never the installer's elevated application setup.
foreach ($name in @('Install-PythonRuntime', 'Get-MachinePython', 'Resolve-ServicePython')) {
    $node = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $node) { throw "Missing helper $name" }
    Invoke-Expression $node.Extent.Text
}
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws($Action, $Expected) {
    try { & $Action } catch { if ($_.Exception.Message -like "*$Expected*") { return }; throw }
    throw "Expected an error containing $Expected"
}
$script:status = 'Valid'
$script:publisher = 'CN=Python Software Foundation, O=Python Software Foundation, C=US'
$script:exitCode = 0
$script:started = 0
$script:downloaded = 0
$script:downloadFailure = $false
function Invoke-WebRequest($Uri, $OutFile, [switch]$UseBasicParsing) {
    Assert-True ($Uri -eq 'https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe') 'Unexpected download origin/version'
    if ($script:downloadFailure) { throw 'Download unavailable' }
    $script:downloaded++
    Set-Content -LiteralPath $OutFile -Value 'mock signed installer'
}
function Get-AuthenticodeSignature($LiteralPath) {
    return [pscustomobject]@{Status=$script:status;SignerCertificate=[pscustomobject]@{Subject=$script:publisher}}
}
function Start-Process($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru) {
    $script:started++
    Assert-True ($ArgumentList -contains 'InstallAllUsers=1') 'Python must install for all users'
    Assert-True ($ArgumentList -contains 'Include_pip=1') 'pip must be installed'
    Assert-True ($ArgumentList -contains 'PrependPath=0') 'Do not change global PATH'
    return [pscustomobject]@{ExitCode=$script:exitCode}
}
$temporary = Join-Path $env:TEMP ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $temporary | Out-Null
$originalProgramFiles = $env:ProgramFiles
try {
    Install-PythonRuntime (Join-Path $temporary 'runtime with spaces') ''
    Assert-True ($script:started -eq 1 -and $script:downloaded -eq 1) 'Bootstrap did not run'
    $script:downloadFailure = $true
    Assert-Throws { Install-PythonRuntime $temporary '' } 'Download unavailable'
    Assert-True ($script:started -eq 1) 'Failed download was executed'
    $script:downloadFailure = $false
    $script:status = 'NotSigned'
    Assert-Throws { Install-PythonRuntime $temporary '' } 'signature'
    Assert-True ($script:started -eq 1) 'Unsigned installer was executed'
    $script:status = 'Valid'
    $script:publisher = 'CN=Other publisher, O=Other publisher, C=US'
    Assert-Throws { Install-PythonRuntime $temporary '' } 'publisher'
    Assert-True ($script:started -eq 1) 'Wrong publisher was executed'
    $script:publisher = 'O=Python Software Foundation, C=US'
    $script:exitCode = 3010
    Assert-Throws { Install-PythonRuntime $temporary '' } 'restart'
    $script:exitCode = 1603
    Assert-Throws { Install-PythonRuntime $temporary '' } '1603'
    $script:exitCode = 0
    $localInstaller = Join-Path $temporary 'python.exe'
    Set-Content $localInstaller 'mock installer'
    $downloadsBefore = $script:downloaded
    Install-PythonRuntime $temporary $localInstaller
    Assert-True ($script:downloaded -eq $downloadsBefore) 'Local installer caused a download'

    # Resolve/runtime-reuse tests do not launch a real interpreter or write Program Files.
    $env:ProgramFiles = $temporary
    $script:installed = 0
    function Install-PythonRuntime($TargetDirectory, $InstallerPath) {
        $script:installed++
        New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
        Set-Content (Join-Path $TargetDirectory 'python.exe') 'mock interpreter'
    }
    function Get-MachinePython { return $null }
    function Invoke-Checked($Executable, $Arguments) { Write-Output 'simulated pip output' }
    $resolved = Resolve-ServicePython '' ''
    Assert-True ($resolved -is [string]) 'Dependency output polluted interpreter path'
    Assert-True ($resolved -eq (Join-Path $temporary 'VSMToolbox-Python\python.exe')) 'Incorrect runtime path'
    $again = Resolve-ServicePython '' ''
    Assert-True ($script:installed -eq 1 -and $again -eq $resolved) 'Existing dedicated runtime not reused'
    Remove-Item (Join-Path $temporary 'VSMToolbox-Python') -Recurse -Force
    function Get-MachinePython { return 'C:\MachinePython\python.exe' }
    $existing = Resolve-ServicePython '' ''
    Assert-True ($existing -eq 'C:\MachinePython\python.exe' -and $script:installed -eq 1) 'Registered machine-wide Python was not reused'
    Assert-Throws { Resolve-ServicePython 'python' 'installer.exe' } 'not both'
    Write-Host 'Installer bootstrap checks passed: signature rejection, download/local input, failure/reboot handling, runtime install/reuse, clean interpreter output, and PowerShell syntax.'
} finally {
    $env:ProgramFiles = $originalProgramFiles
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
