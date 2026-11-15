$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$web = Join-Path $PSScriptRoot 'web_app.py'
$downloader = Join-Path $PSScriptRoot 'downloader_app.py'

# 统一入口：管理平台 8765，下载器 8766；播放器由管理平台的 /player/ 提供。
$env:DOUYIN_HOST = '0.0.0.0'
$env:DOUYIN_PORT = '8765'
$env:DOUYIN_DOWNLOADER_HOST = '0.0.0.0'
$env:DOUYIN_DOWNLOADER_PORT = '8766'
$env:PYTHONIOENCODING = 'utf-8'

function Test-Port([int]$Port) {
    try { return (Test-NetConnection -ComputerName '127.0.0.1' -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { return $false }
}

if (-not (Test-Port 8766)) {
    Start-Process -FilePath $python -ArgumentList @($downloader) -WorkingDirectory $root -WindowStyle Minimized
    Start-Sleep -Seconds 2
}
if (-not (Test-Port 8765)) {
    Start-Process -FilePath $python -ArgumentList @($web) -WorkingDirectory $root -WindowStyle Minimized
    Start-Sleep -Seconds 2
}

Write-Host '统一工作台已启动：' -ForegroundColor Green
Write-Host '管理平台 / 播放器: http://127.0.0.1:8765/'
Write-Host '下载器:             http://127.0.0.1:8766/'
Write-Host '手机访问时使用本机局域网 IP 替换 127.0.0.1。'
Start-Process 'http://127.0.0.1:8765/'
