# Pass arguments directly to WSL; no user value is interpreted as shell code.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Convert-ToLinuxPath([string]$Path) {
    if ($Path.StartsWith('/')) { return $Path }
    $absolutePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $linuxPath = & wsl.exe --distribution Ubuntu --exec wslpath -a -u $absolutePath
    if ($LASTEXITCODE -ne 0) { throw "Cannot translate path for WSL: $Path" }
    return [string]$linuxPath
}

$linuxRoot = Convert-ToLinuxPath $projectRoot
$forward = [System.Collections.Generic.List[string]]::new()
if ($args.Count -eq 0 -or ([string]$args[0]).StartsWith('-')) {
    $forward.Add('human')
}
foreach ($argument in $args) { $forward.Add([string]$argument) }
if ($forward[0] -in @('human', 'watch') -and '--rgb' -notin $forward) {
    $forward.Add('--rgb')
}
if ($forward[0] -eq 'human' -and '--start-paused' -notin $forward) {
    $forward.Add('--start-paused')
}

for ($i = 0; $i -lt $forward.Count; $i++) {
    if ($forward[$i] -eq '--replay' -and $i + 1 -lt $forward.Count `
        -and -not $forward[$i + 1].StartsWith('-')) {
        $i++
        $forward[$i] = Convert-ToLinuxPath $forward[$i]
    } elseif ($forward[$i].StartsWith('--replay=')) {
        $forward[$i] = '--replay=' + (Convert-ToLinuxPath $forward[$i].Substring(9))
    }
}

& wsl.exe --distribution Ubuntu --cd $linuxRoot --exec env `
    'SC2PATH=/home/nyuny/sc2/StarCraftII' `
    'LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6' `
    'LP_NUM_THREADS=4' `
    /home/nyuny/.venvs/sc2-arena/bin/python -m sc2.run @forward
exit $LASTEXITCODE
