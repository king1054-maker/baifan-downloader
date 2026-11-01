#requires -Version 5.1
# BaiFan Downloader: Windows x64 release installer for humans and AI agents.
# No Python, Git, compiler or administrator rights required for Portable mode.
# Portable mode writes only to the selected workspace; it does not register the app.

[CmdletBinding()]
param(
    [ValidateSet('Auto','Portable','Setup')]
    [string]$Mode = 'Auto',
    [string]$Destination = '',
    [string]$Tag = '',
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'BaiFan preview currently supports Windows x64 only.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'BaiFan preview currently supports Windows only.'
}

$repo = 'king1054-maker/baifan-downloader'
$api = "https://api.github.com/repos/$repo/releases?per_page=30"
$headers = @{ 'User-Agent'='BaiFan-Installer/1.0'; 'Accept'='application/vnd.github+json' }
Write-Host 'Finding the newest published BaiFan release...'
# Windows PowerShell 5.1 may deliver a REST JSON array as a single pipeline
# item. Enumerate the returned array directly, not via @(Invoke-RestMethod).
$releases = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 30
$release = $null
foreach ($candidate in $releases) {
    if ($candidate.draft) { continue }
    if ($Tag -and $candidate.tag_name -ne $Tag) { continue }
    $assets = @($candidate.assets)
    $sum = @($assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1)
    $setup = @($assets | Where-Object { $_.name -match '^baifan-downloader-.*-setup\.exe$' } | Select-Object -First 1)
    $portable = @($assets | Where-Object { $_.name -match '^baifan-downloader-.*-portable-win-x64\.zip$' } | Select-Object -First 1)
    if ($sum.Count -gt 0 -and ($setup.Count -gt 0 -or $portable.Count -gt 0)) {
        $release = $candidate
        break
    }
}
if ($null -eq $release) {
    throw 'No published Windows release with SHA256SUMS.txt was found. Check the Releases page; do not compile source.'
}

$asset = $null
$chosen = $Mode
if ($chosen -eq 'Auto') {
    # AI agents often cannot interact with the Inno Setup GUI; prefer unzip-and-run.
    $chosen = 'Portable'
}
if ($chosen -eq 'Portable') {
    $asset = @($release.assets | Where-Object { $_.name -match '^baifan-downloader-.*-portable-win-x64\.zip$' } | Select-Object -First 1)
    if ($asset.Count -eq 0) {
        throw "Release $($release.tag_name) has no portable ZIP. Use -Mode Setup on a normal interactive Windows desktop."
    }
    $asset = $asset[0]
} else {
    $asset = @($release.assets | Where-Object { $_.name -match '^baifan-downloader-.*-setup\.exe$' } | Select-Object -First 1)
    if ($asset.Count -eq 0) {
        throw "Release $($release.tag_name) has no Windows setup.exe."
    }
    $asset = $asset[0]
}
$sumAsset = @($release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1)[0]

$root = if ($Destination) {
    [IO.Path]::GetFullPath($Destination)
} else {
    Join-Path (Get-Location).Path 'BaiFanApps'
}
New-Item -ItemType Directory -Path $root -Force | Out-Null
$cache = Join-Path $root '.download'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$archive = Join-Path $cache $asset.name

Write-Host ("Release: " + $release.tag_name)
Write-Host ("Mode: " + $chosen)
Write-Host ("Downloading: " + $asset.name)
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive -UseBasicParsing -TimeoutSec 1800
# Windows PowerShell 5.1 may expose Content as byte[] for GitHub assets.
# Download to a file and decode deterministically instead of casting to string.
$checksumFile = Join-Path $cache 'SHA256SUMS.txt'
Invoke-WebRequest -Uri $sumAsset.browser_download_url -OutFile $checksumFile -UseBasicParsing -TimeoutSec 30
$sums = [IO.File]::ReadAllText($checksumFile, [Text.Encoding]::ASCII)
$re = '(?im)^([a-f0-9]{64})\s+\*?' + [regex]::Escape($asset.name) + '\s*
$match = [regex]::Match([string]$sums, $re)
if (-not $match.Success) { throw "SHA256SUMS.txt has no valid entry for $($asset.name)" }
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $match.Groups[1].Value.ToLowerInvariant()) {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    throw 'SHA256 verification failed. Download was removed; do not execute.'
}
Write-Host 'SHA256 verified.'

if ($chosen -eq 'Setup') {
    if ($NoLaunch) {
        Write-Host "Verified setup.exe: $archive"
        return
    }
    Write-Host 'Launching the interactive installer. A user may need to approve Windows prompts.'
    try {
        Start-Process -FilePath $archive -WorkingDirectory $cache | Out-Null
    } catch {
        throw "Installer could not launch. Verified file is at: $archive. $($_.Exception.Message)"
    }
    Write-Host "Installer opened; complete the on-screen wizard. Location: $archive"
    return
}

$safeTag = $release.tag_name -replace '[^a-zA-Z0-9._-]','_'
$target = Join-Path $root "BaiFanDownloader-$safeTag"
if (Test-Path $target) {
    throw "Target already exists: $target. Stop instead of deleting user downloads or settings."
}
$temp = Join-Path $root ('.extract-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $temp -ErrorAction Stop
    $source = Join-Path $temp 'BaiFanDownloader'
    $exe = Join-Path $source 'BaiFanDownloader.exe'
    $engine = Join-Path $source '_internal\bin\aria2c.exe'
    if (-not (Test-Path $exe) -or -not (Test-Path $engine)) {
        throw 'Portable ZIP is incomplete (app or aria2c missing).'
    }
    Move-Item -LiteralPath $source -Destination $target -ErrorAction Stop
} finally {
    if (Test-Path $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$exe = Join-Path $target 'BaiFanDownloader.exe'
Write-Host "Downloaded, verified and extracted: $exe"
if ($NoLaunch) { return }
try {
    $proc = Start-Process -FilePath $exe -WorkingDirectory $target -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 2
    $proc.Refresh()
    if ($proc.HasExited) { throw "Application exited immediately (code $($proc.ExitCode))." }
    Write-Host "Started BaiFanDownloader (PID $($proc.Id))."
} catch {
    throw "The program is extracted but this environment cannot start it. User can run: $exe. $($_.Exception.Message)"
}

$match = [regex]::Match([string]$sums, $re)
if (-not $match.Success) { throw "SHA256SUMS.txt has no valid entry for $($asset.name)" }
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $match.Groups[1].Value.ToLowerInvariant()) {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    throw 'SHA256 verification failed. Download was removed; do not execute.'
}
Write-Host 'SHA256 verified.'

if ($chosen -eq 'Setup') {
    if ($NoLaunch) {
        Write-Host "Verified setup.exe: $archive"
        return
    }
    Write-Host 'Launching the interactive installer. A user may need to approve Windows prompts.'
    try {
        Start-Process -FilePath $archive -WorkingDirectory $cache | Out-Null
    } catch {
        throw "Installer could not launch. Verified file is at: $archive. $($_.Exception.Message)"
    }
    Write-Host "Installer opened; complete the on-screen wizard. Location: $archive"
    return
}

$safeTag = $release.tag_name -replace '[^a-zA-Z0-9._-]','_'
$target = Join-Path $root "BaiFanDownloader-$safeTag"
if (Test-Path $target) {
    throw "Target already exists: $target. Stop instead of deleting user downloads or settings."
}
$temp = Join-Path $root ('.extract-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $temp -ErrorAction Stop
    $source = Join-Path $temp 'BaiFanDownloader'
    $exe = Join-Path $source 'BaiFanDownloader.exe'
    $engine = Join-Path $source '_internal\bin\aria2c.exe'
    if (-not (Test-Path $exe) -or -not (Test-Path $engine)) {
        throw 'Portable ZIP is incomplete (app or aria2c missing).'
    }
    Move-Item -LiteralPath $source -Destination $target -ErrorAction Stop
} finally {
    if (Test-Path $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$exe = Join-Path $target 'BaiFanDownloader.exe'
Write-Host "Downloaded, verified and extracted: $exe"
if ($NoLaunch) { return }
try {
    $proc = Start-Process -FilePath $exe -WorkingDirectory $target -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 2
    $proc.Refresh()
    if ($proc.HasExited) { throw "Application exited immediately (code $($proc.ExitCode))." }
    Write-Host "Started BaiFanDownloader (PID $($proc.Id))."
} catch {
    throw "The program is extracted but this environment cannot start it. User can run: $exe. $($_.Exception.Message)"
}
