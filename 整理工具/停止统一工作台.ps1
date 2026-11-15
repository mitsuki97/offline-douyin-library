$ErrorActionPreference = 'SilentlyContinue'
foreach ($port in 8765, 8766) {
    Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Stop-Process -Id $_ -Force
    }
}
Write-Host '统一工作台已停止。'
