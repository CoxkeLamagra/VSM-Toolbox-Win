#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$service = Get-Service VSMToolbox -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -ne 'Stopped') { Stop-Service VSMToolbox }
    & sc.exe delete VSMToolbox
    if ($LASTEXITCODE -ne 0) { throw 'Service removal failed.' }
}
Get-NetFirewallRule -Name 'VSMToolbox-HTTP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host 'Service and managed firewall rule removed. Application files, Python, and all local data/backups were preserved.'
