# Run both participants with the installed Windows engine, using its native UI.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.deps/sc2-win/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Windows Python environment missing. Run sc2/setup/windows.ps1 first.'
}
if (-not $env:SC2PATH) { $env:SC2PATH = 'C:\Program Files (x86)\StarCraft II' }
$forward = [System.Collections.Generic.List[string]]::new()
if ($args.Count -eq 0 -or ([string]$args[0]).StartsWith('-')) { $forward.Add('human') }
foreach ($argument in $args) { $forward.Add([string]$argument) }
if ('--version' -notin $forward -and -not ($forward | Where-Object { $_.StartsWith('--version=') })) {
    $forward.Add('--version'); $forward.Add('latest')
}
if ($forward[0] -in @('human', 'watch') -and '--native-ui' -notin $forward) {
    $forward.Add('--native-ui')
}
if ($forward[0] -eq 'human' -and '--start-paused' -notin $forward) { $forward.Add('--start-paused') }
# Resolve caller-relative replay paths before changing to the project directory.
for ($i = 0; $i -lt $forward.Count; $i++) {
    if ($forward[$i] -eq '--replay' -and $i + 1 -lt $forward.Count) {
        $i++
        $forward[$i] = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($forward[$i])
    } elseif ($forward[$i].StartsWith('--replay=')) {
        $forward[$i] = '--replay=' + $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($forward[$i].Substring(9))
    }
}
$env:PYTHONUTF8 = '1'
Push-Location $projectRoot
try { & $python -m sc2.run @forward; $result = $LASTEXITCODE }
finally { Pop-Location }
exit $result
