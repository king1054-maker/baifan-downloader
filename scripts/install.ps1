#requires -Version 5.1
# Legacy entrypoint. The canonical installer is now the repository-root install.ps1.
[CmdletBinding()]
param(
    [ValidateSet('Auto','Portable','Setup')]
    [string]$Mode = 'Auto',
    [string]$Destination = '',
    [string]$Tag = '',
    [switch]$NoLaunch
)
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'install.ps1'
if (-not (Test-Path -LiteralPath $entry)) { throw 'Root install.ps1 not found.' }
& $entry @PSBoundParameters
if (-not $?) { throw 'BaiFan installation entrypoint failed.' }
