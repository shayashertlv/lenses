$ErrorActionPreference = 'Stop'
$modelingRoot = $PSScriptRoot
Set-Location -LiteralPath $modelingRoot
$python = Join-Path $modelingRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $modelingRoot 'dist\index.html'))) { throw 'Run npm run build first.' }
$localTemp = Join-Path $modelingRoot 'data\tmp'
New-Item -ItemType Directory -Path $localTemp -Force | Out-Null
$env:TEMP = $localTemp
$env:TMP = $localTemp
$env:PYTHONNOUSERSITE = '1'
& $python -m app.launch
if ($LASTEXITCODE -ne 0) { throw "Modeling Auto exited with code $LASTEXITCODE" }
