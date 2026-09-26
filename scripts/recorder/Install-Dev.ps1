param(
  [Parameter(Mandatory = $true)]
  [string]$Repository,
  [string]$InstallDir = "$env:LOCALAPPDATA\meshAgent\bin"
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Repository = (Resolve-Path $Repository).Path
$Version = (git -C $Root rev-parse --short HEAD 2>$null)
if (-not $Version) { $Version = "dev" }

$LegacyQueue = "$HOME\.meshagent\queues"
if ((Test-Path $LegacyQueue) -and (Get-ChildItem $LegacyQueue -Recurse -File | Where-Object { $_.Length -gt 0 -and ($_.Name -like "*.jsonl" -or $_.Name -like "*.jsonl.inflight") } | Select-Object -First 1)) {
  throw "Undelivered Python recorder batches exist. Stop Cursor, run 'meshagent replay --json', and retry only after retained is 0."
}

$Go = Get-Command go.exe -ErrorAction SilentlyContinue
if (-not $Go) { throw "Go 1.25.13 or newer is required." }
$GoVersion = (& $Go.Source version)
$VersionMatch = [regex]::Match($GoVersion, 'go1\.(\d+)\.(\d+)')
if (-not $VersionMatch.Success) { throw "Could not determine the Go version." }
$Minor = [int]$VersionMatch.Groups[1].Value
$Patch = [int]$VersionMatch.Groups[2].Value
if (($Minor -lt 25) -or ($Minor -eq 25 -and $Patch -lt 13)) {
  throw "Go 1.25.13 or newer is required; found $GoVersion"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Push-Location "$Root\recorder"
try {
  go test ./...
  go build -trimpath -ldflags "-s -w -X main.version=$Version" -o "$InstallDir\meshagent-recorder.exe" ./cmd/meshagent-recorder
  go build -trimpath -ldflags "-s -w -X main.version=$Version" -o "$InstallDir\meshagent-hook.exe" ./cmd/meshagent-hook
} finally { Pop-Location }

$TaskName = "meshAgent Recorder (Stage 1A Development)"
$Action = New-ScheduledTaskAction -Execute "$InstallDir\meshagent-recorder.exe" -Argument "serve"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$Healthy = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
  try {
    $Health = (& "$InstallDir\meshagent-hook.exe" --health | ConvertFrom-Json)
    if ($Health.status -eq "ok") { $Healthy = $true; break }
  } catch { }
  Start-Sleep -Milliseconds 200
}
if (-not $Healthy) { throw "The native recorder task did not become healthy." }

$Python = Get-Command python.exe -ErrorAction SilentlyContinue
$PythonPrefix = @()
if (-not $Python) {
  $Python = Get-Command py.exe -ErrorAction SilentlyContinue
  $PythonPrefix = @("-3")
}
if (-not $Python) { throw "Python 3 is required to run the safe Cursor hook configurator." }
& $Python.Source @PythonPrefix "$Root\scripts\demo\configure_cursor_hooks.py" $Repository --native-hook "$InstallDir\meshagent-hook.exe" --local-exclude
if ($LASTEXITCODE -ne 0) { throw "Cursor repository configuration failed." }

& "$InstallDir\meshagent-recorder.exe" status
Write-Host "Installed and verified Stage 1A development binaries in $InstallDir."
Write-Host "Configured Cursor recording for $Repository. Fully quit Cursor and reopen that repository root."
Write-Host "Production code signing, enterprise deployment, and OS-keystore queue keys remain Stage 1B."
