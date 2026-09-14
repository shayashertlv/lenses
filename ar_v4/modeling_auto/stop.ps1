$ErrorActionPreference = 'Stop'
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8060/api/health' -TimeoutSec 5
if ($health.runtime_version -notlike 'modeling-auto-*') { throw 'Port 8060 is not Modeling Auto; nothing was stopped.' }
if ($health.active_job_id) { throw 'Cancel the active run in Modeling Auto and wait until it stops, then run this command again.' }
Invoke-RestMethod -Uri 'http://127.0.0.1:8060/api/shutdown' -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 5
