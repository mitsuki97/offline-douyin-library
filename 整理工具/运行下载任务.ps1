param([Parameter(Mandatory=$true)][string]$Task)
$ErrorActionPreference='Stop'
$py='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py (Join-Path $PSScriptRoot 'download_task.py') $Task
if($LASTEXITCODE -ne 0){throw '下载任务失败，请保留任务文件和输出信息。'}
