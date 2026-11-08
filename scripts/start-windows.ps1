# Native Windows play, replay, or one participant in a LAN room.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launchArguments = @($args)
if ('--help' -notin $launchArguments -and '-h' -notin $launchArguments) {
    . (Join-Path $PSScriptRoot 'setup/config.ps1')
    Initialize-Sc2Path -ProjectRoot $projectRoot
}
& (Join-Path $PSScriptRoot 'setup/windows.ps1')
$python = Join-Path $projectRoot '.deps/sc2-win/Scripts/python.exe'
$forward = [System.Collections.Generic.List[string]]::new()
if ($launchArguments.Count -eq 0 -or ([string]$launchArguments[0]).StartsWith('-')) { $forward.Add('human') }
foreach ($argument in $launchArguments) { $forward.Add([string]$argument) }
# An explicit --ai skips the selection menu.
$hasAi = '--ai' -in $forward -or ($forward | Where-Object { $_.StartsWith('--ai=') })
$localAi = '--control=ai' -in $forward
for ($i = 0; $i + 1 -lt $forward.Count; $i++) {
    if ($forward[$i] -eq '--control' -and $forward[$i + 1] -eq 'ai') { $localAi = $true }
}
if (($forward[0] -eq 'human' -or ($forward[0] -in @('host', 'join') -and $localAi)) `
    -and -not $hasAi -and '--help' -notin $forward -and '-h' -notin $forward -and '--yes' -notin $forward) {
    Write-Host 'Choose AI: 1 = Original (default), 2 = A (finals champion), 3 = B (finalist), 4 = A2 (human-match revision), 5 = A3 (ranged endgame revision), 6 = A4 (retreat and rescue revision)'
    do {
        $choice = Read-Host 'AI [1/2/3/4/5/6, default/a/b/a2/a3/a4; Enter = 1]'
        $choice = if ($choice) { $choice.Trim().ToLowerInvariant() } else { '' }
        $selectedAi = switch ($choice) {
            '' { 'default' } '1' { 'default' } 'default' { 'default' }
            '2' { 'a' } 'a' { 'a' } '3' { 'b' } 'b' { 'b' }
            '4' { 'a2' } 'a2' { 'a2' }
            '5' { 'a3' } 'a3' { 'a3' }
            '6' { 'a4' } 'a4' { 'a4' }
            default { Write-Host 'Please enter 1, 2, 3, 4, 5, 6, default, a, b, a2, a3 or a4.'; $null }
        }
    } while (-not $selectedAi)
    $forward.Add('--ai'); $forward.Add($selectedAi)
}
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
$module = if ($forward[0] -in @('host', 'join')) { 'sc2.lan' } else { 'sc2.run' }
try { & $python -m $module @forward; $result = $LASTEXITCODE }
finally { Pop-Location }
exit $result
