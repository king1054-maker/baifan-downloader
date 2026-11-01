#requires -Version 5.1
<#
 白饭下载器一键安装脚本
 用法：
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Repo "你的GitHub用户名/baifan-downloader" -Tag "v0.10.103-preview1"
#>
param(
    [string]$Repo = "king1054-maker/baifan-downloader",
    [string]$Tag = "v0.10.103-preview1",
    [switch]$SkipHashCheck
)

$ErrorActionPreference = "Stop"
# Preview release tag includes a suffix, while the installer's filename uses the base app version.
$matched = [regex]::Match($Tag, '^v?(\d+\.\d+\.\d+)')
if (-not $matched.Success) { throw "不支持的发行标签: $Tag" }
$asset = "baifan-downloader-$($matched.Groups[1].Value)-setup.exe"
$base = "https://github.com/$Repo/releases/download/$Tag"
$tmp = Join-Path $env:TEMP ("baifan-" + [guid]::NewGuid().ToString("N") + ".exe")

Write-Host "下载 $base/$asset ..."
Invoke-WebRequest -Uri "$base/$asset" -OutFile $tmp -UseBasicParsing
if (-not (Test-Path $tmp)) { throw "下载失败" }

if (-not $SkipHashCheck) {
    # Fail closed: checksum must exist and match; never silently bypass a missing or broken checksum.
    $sums = Invoke-WebRequest -Uri "$base/SHA256SUMS.txt" -UseBasicParsing |
        Select-Object -ExpandProperty Content
    $line = ($sums -split "`r?`n") | Where-Object { $_ -match [regex]::Escape($asset) } | Select-Object -First 1
    if (-not $line) { throw "SHA256SUMS.txt 内缺少安装包的校验记录" }
    $expected = ($line -split "\s+")[0].ToLower()
    if ($expected -notmatch '^[a-f0-9]{64}$') { throw "SHA256SUMS.txt 格式不正确" }
    $actual = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "SHA256 校验失败，已停止安装" }
    Write-Host "SHA256 校验通过"
}

Write-Host "开始静默安装..."
$p = Start-Process -FilePath $tmp -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" -Wait -PassThru
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0) { throw "安装程序返回错误码 $($p.ExitCode)" }
Write-Host "安装完成。"
