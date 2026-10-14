param(
    [string]$GameDir
)

$ErrorActionPreference = 'Stop'
$plannerRoot = $PSScriptRoot
$plannerPort = 18765
$plannerUrl = "http://127.0.0.1:$plannerPort/"
$plannerGameDir = if ([string]::IsNullOrWhiteSpace($GameDir)) {
    Split-Path -Parent $plannerRoot
} else {
    [System.IO.Path]::GetFullPath($GameDir)
}
if (-not (Test-Path -LiteralPath $plannerGameDir -PathType Container)) {
    throw "Game directory was not found: $plannerGameDir"
}

try {
    $plannerHealth = Invoke-RestMethod -Uri ($plannerUrl + 'api/health') -TimeoutSec 2
    if ($plannerHealth.app -eq 'lingqi-planner' -and $plannerHealth.gameDir -eq $plannerGameDir) {
        Start-Process $plannerUrl
        exit 0
    }
    throw 'Port 18765 is already used by another application.'
} catch {
    if ($_.Exception.Message -match 'already used') { throw }
}

$plannerCandidates = @(
    (Join-Path $plannerRoot 'runtime\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$plannerPython = $plannerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $plannerPython) {
    $plannerCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($plannerCommand) { $plannerPython = $plannerCommand.Source }
}
if (-not $plannerPython) { throw 'Python 3.12 was not found. See README.md for setup instructions.' }
if (-not (Test-Path -LiteralPath (Join-Path $plannerRoot 'vendor\ortools'))) {
    throw "Solver dependencies are missing. Run: `"$plannerPython`" -m pip install --target .\vendor -r .\requirements.txt. See README.md for the full setup."
}
$plannerOut = Join-Path $plannerRoot 'server.stdout.log'
$plannerErr = Join-Path $plannerRoot 'server.stderr.log'
$plannerArgs = @('-X', 'utf8', ('"' + (Join-Path $plannerRoot 'server.py') + '"'), '--game-dir', ('"' + $plannerGameDir + '"'), '--no-browser', '--port', "$plannerPort")
$plannerProcess = Start-Process -FilePath $plannerPython -ArgumentList $plannerArgs -WorkingDirectory $plannerRoot -WindowStyle Hidden -RedirectStandardOutput $plannerOut -RedirectStandardError $plannerErr -PassThru
$plannerIdentity = @{pid=$plannerProcess.Id;script=(Join-Path $plannerRoot 'server.py');port=$plannerPort;startedAt=(Get-Date).ToString('o')}
$plannerIdentity | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $plannerRoot 'server-process.json') -Encoding utf8
for ($plannerAttempt = 0; $plannerAttempt -lt 40; $plannerAttempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $plannerHealth = Invoke-RestMethod -Uri ($plannerUrl + 'api/health') -TimeoutSec 1
        if ($plannerHealth.app -eq 'lingqi-planner' -and $plannerHealth.pid -eq $plannerProcess.Id) {
            Start-Process $plannerUrl
            exit 0
        }
    } catch { }
    $plannerProcess.Refresh()
    if ($plannerProcess.HasExited) { break }
}
throw "Planner startup failed. Read $plannerErr"
