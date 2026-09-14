$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$localTemp = Join-Path $PSScriptRoot 'data\tmp'
$pipCache = Join-Path $PSScriptRoot 'data\cache\pip'
New-Item -ItemType Directory -Path $localTemp,$pipCache -Force | Out-Null
$env:TEMP = $localTemp
$env:TMP = $localTemp
$env:PIP_CACHE_DIR = $pipCache
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 virtual environment creation failed.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm ci --cache data/cache/npm
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Output 'Setup complete. Run .\start.ps1, then open http://127.0.0.1:8060.'
