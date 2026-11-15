param(
    [ValidateSet('报告','导入','核对','恢复','补资料','导入复核','网页')]
    [string]$Action = '报告',
    [string]$Batch,
    [string]$ReviewFile
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$libraryPython = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$libraryScript = Join-Path $PSScriptRoot 'library.py'
$libraryRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '本地视频库'
function Run-Library {
    & $libraryPython $libraryScript @args
    if ($LASTEXITCODE -ne 0) { throw '处理停止，请保留现场和操作记录，不要手工覆盖文件。' }
}
switch ($Action) {
    '报告' { Invoke-Item -LiteralPath (Join-Path $libraryRoot '报告\开始查看.html') }
    '导入' {
        Run-Library scan
        Run-Library media
        Run-Library plan
        Run-Library apply
        Run-Library similar
        Run-Library verify
        Run-Library report
    }
    '核对' { Run-Library verify; Run-Library report }
    '恢复' {
        if (-not $Batch) { throw '请填写需要恢复的批次编号，见原始清单名称。' }
        Run-Library rollback --batch $Batch
        Run-Library verify
        Run-Library report
    }
    '补资料' { Run-Library enrich --limit 10; Run-Library report }
    '导入复核' {
        if (-not $ReviewFile) { throw '请填写从相似视频对照报告下载的复核文件路径。' }
        Run-Library import-reviews --input $ReviewFile
        Run-Library report
    }
    '网页' {
        $webScript = Join-Path $PSScriptRoot 'web_app.py'
        Start-Process -FilePath $libraryPython -ArgumentList @($webScript) -WorkingDirectory (Split-Path $PSScriptRoot -Parent) -WindowStyle Hidden
        Start-Sleep -Seconds 2
        Start-Process 'http://127.0.0.1:8765'
    }
}
