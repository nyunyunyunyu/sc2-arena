# Native Windows play, replay, or one participant in a LAN room.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launchArguments = @($args)
$lobby = $launchArguments.Count -gt 0 -and $launchArguments[0] -eq 'lobby'
$guiLobby = $lobby -and '--help' -notin $launchArguments -and '-h' -notin $launchArguments
try {
    if ('--help' -notin $launchArguments -and '-h' -notin $launchArguments) {
        . (Join-Path $PSScriptRoot 'setup/config.ps1')
        Initialize-Sc2Path -ProjectRoot $projectRoot -Graphical:$guiLobby
    }
    if ($guiLobby) {
        $logDirectory = Join-Path $projectRoot 'sc2/runs'
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
        $setupError = Join-Path $logDirectory 'lobby-setup-error.log'
        # Windows PowerShell treats redirected native stderr as an error; capture it at process level.
        $setup = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                ('"' + (Join-Path $PSScriptRoot 'setup/windows.ps1') + '"')) `
            -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'lobby-setup.log') `
            -RedirectStandardError $setupError
        if ($setup.ExitCode -ne 0) { throw (Get-Content -LiteralPath $setupError -Raw) }
    } else { & (Join-Path $PSScriptRoot 'setup/windows.ps1') }
} catch {
    if (-not $guiLobby) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '联机大厅启动失败') | Out-Null
    exit 1
}
$python = Join-Path $projectRoot $(if ($guiLobby) { '.deps/sc2-win/Scripts/pythonw.exe' } else { '.deps/sc2-win/Scripts/python.exe' })
$forward = [System.Collections.Generic.List[string]]::new()
if ($launchArguments.Count -eq 0 -or ([string]$launchArguments[0]).StartsWith('-')) { $forward.Add('human') }
foreach ($argument in $launchArguments) { $forward.Add([string]$argument) }
if ($lobby) { $forward.RemoveAt(0) }
# An explicit --ai skips the selection menu.
$hasAi = '--ai' -in $forward -or ($forward | Where-Object { $_.StartsWith('--ai=') })
$localAi = '--control=ai' -in $forward
for ($i = 0; $i + 1 -lt $forward.Count; $i++) {
    if ($forward[$i] -eq '--control' -and $forward[$i + 1] -eq 'ai') { $localAi = $true }
}
if (($forward[0] -eq 'human' -or ($forward[0] -in @('host', 'join') -and $localAi)) `
    -and -not $hasAi -and '--help' -notin $forward -and '-h' -notin $forward -and '--yes' -notin $forward) {
    Write-Host 'Choose AI: 1 = Original (default), 2 = A (finals champion), 3 = B (finalist), 4 = A2 (human-match revision), 5 = A3 (ranged endgame revision), 6 = A4 (retreat and rescue revision), 7 = A5 (trial, 22/24 regression wins), 8 = A6 (24/24 plus 4/4 vs A5)'
    do {
        $choice = Read-Host 'AI [1/2/3/4/5/6/7/8, default/a/b/a2/a3/a4/a5/a6; Enter = 1]'
        $choice = if ($choice) { $choice.Trim().ToLowerInvariant() } else { '' }
        $selectedAi = switch ($choice) {
            '' { 'default' } '1' { 'default' } 'default' { 'default' }
            '2' { 'a' } 'a' { 'a' } '3' { 'b' } 'b' { 'b' }
            '4' { 'a2' } 'a2' { 'a2' }
            '5' { 'a3' } 'a3' { 'a3' }
            '6' { 'a4' } 'a4' { 'a4' }
            '7' { 'a5' } 'a5' { 'a5' }
            '8' { 'a6' } 'a6' { 'a6' }
            default { Write-Host 'Please enter 1, 2, 3, 4, 5, 6, 7, 8, default, a, b, a2, a3, a4, a5 or a6.'; $null }
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
$module = if ($lobby) { 'sc2.lan_ui' } elseif ($forward[0] -in @('host', 'join')) { 'sc2.lan' } else { 'sc2.run' }
try {
    if ($guiLobby) {
        # A pipeline makes PowerShell wait for GUI executables and collect their exit code.
        & $python -m $module @forward | Out-Host
    } else { & $python -m $module @forward }
    $result = $LASTEXITCODE
}
finally { Pop-Location }
if ($guiLobby -and $result -ne 0) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('大厅未能启动，请查看 sc2/runs/lobby-ui.log。', '联机大厅启动失败') | Out-Null
}
exit $result
