$ErrorActionPreference='Stop'
$py='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:DOUYIN_DOWNLOADER_HOST='0.0.0.0'
& $py (Join-Path $PSScriptRoot 'downloader_app.py')
