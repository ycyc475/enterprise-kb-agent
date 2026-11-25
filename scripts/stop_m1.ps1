$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
foreach ($service in @('frontend','backend')) {
    $pidFile = Join-Path $projectRoot "data/runtime/$service.pid"
    if (!(Test-Path -LiteralPath $pidFile)) { continue }
    $serviceProcessId = 0
    if (![int]::TryParse([IO.File]::ReadAllText($pidFile).Trim(), [ref]$serviceProcessId)) { throw "Invalid process record: $service" }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction SilentlyContinue
    if (!$process) { continue }
    $expected = if ($service -eq 'backend') { '-m app.cli serve' } else { (Join-Path $projectRoot 'frontend/node_modules/vite/bin/vite.js') }
    if ($service -eq 'backend') {
        $expectedExecutable = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
        if ($process.ExecutablePath -ne $expectedExecutable -or !$process.CommandLine.Contains($expected)) { throw 'Backend process identity changed; refusing to stop it.' }
    } elseif (!$process.CommandLine.Contains($expected)) { throw 'Frontend process identity changed; refusing to stop it.' }
    Stop-Process -Id $serviceProcessId -ErrorAction Stop
    Write-Output "Stopped $service (PID $serviceProcessId)."
}
Write-Output 'Application services stopped. Docker database and data are preserved.'
