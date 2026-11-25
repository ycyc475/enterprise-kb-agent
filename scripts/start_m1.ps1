param([int]$ApiPort = 8000, [int]$WebPort = 5173)
$ErrorActionPreference = 'Stop'
if ($ApiPort -ne 8000 -or $WebPort -ne 5173) { throw 'M6 uses API port 8000 and web port 5173. Update proxy and allowed origins together before changing ports.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$runtimeRoot = Join-Path $projectRoot 'data/runtime'
$pythonPath = Join-Path $backendRoot '.venv/Scripts/python.exe'
$vitePath = Join-Path $frontendRoot 'node_modules/vite/bin/vite.js'
if (!(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath $vitePath)) {
    throw 'Install backend and frontend dependencies first. See README.'
}
$nodePath = (Get-Command node -ErrorAction Stop).Source
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

function Test-Listening([int]$PortNumber) {
    return [bool](Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue)
}

if (Test-Listening $ApiPort) {
    try { $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 4 } catch { throw "Port $ApiPort is occupied. Stop the existing service before starting M6." }
    if ($health.service -ne 'enterprise-kb-agent' -or $health.phase -ne 'M6') { throw "Port $ApiPort belongs to another application." }
    Write-Output 'M6 API is already running.'
} else {
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','app.cli','serve','--port',"$ApiPort") -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot 'backend.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'backend.stderr.log') -PassThru
    [IO.File]::WriteAllText((Join-Path $runtimeRoot 'backend.pid'), [string]$process.Id)
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try { $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 1; if ($health.phase -eq 'M6') { $ready = $true; break } } catch { Start-Sleep -Milliseconds 400 }
    }
    if (!$ready) { throw 'API did not become ready. Check data/runtime/backend.stderr.log.' }
}

if (Test-Listening $WebPort) {
    try { $page = Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 4 } catch { throw "Web port $WebPort is occupied." }
    if ($page.Content -notmatch '/src/main.ts') { throw "Web port $WebPort belongs to another application." }
    Write-Output 'Web server is already running.'
} else {
    if ($ApiPort -ne 8000) { throw 'The Vite API proxy currently targets port 8000. Use the default API port.' }
    $process = Start-Process -FilePath $nodePath -ArgumentList @(('"' + $vitePath + '"'),'--host','127.0.0.1','--port',"$WebPort",'--strictPort') -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot 'frontend.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'frontend.stderr.log') -PassThru
    [IO.File]::WriteAllText((Join-Path $runtimeRoot 'frontend.pid'), [string]$process.Id)
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try { $page = Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 1; $ready = $true; break } catch { Start-Sleep -Milliseconds 400 }
    }
    if (!$ready) { throw 'Web server did not become ready. Check data/runtime/frontend.stderr.log.' }
}
Write-Output "Web: http://127.0.0.1:$WebPort"
Write-Output "API docs: http://127.0.0.1:$ApiPort/docs"
Write-Output 'Demo credentials: data/runtime/demo-credentials.json (keep this file local).'
