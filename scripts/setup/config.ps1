function Initialize-Sc2Path {
    param([Parameter(Mandatory=$true)][string]$ProjectRoot, [switch]$Graphical)
    $envFile = Join-Path $ProjectRoot '.env'
    $lines = @()
    if (Test-Path -LiteralPath $envFile) { $lines = @(Get-Content -LiteralPath $envFile -Encoding UTF8) }
    $candidate = $env:SC2PATH
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        foreach ($line in $lines) {
            if ($line -match '^\s*SC2PATH\s*=\s*(.*)$') { $candidate = $Matches[1].Trim() }
        }
    }
    while ($true) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            if ($Graphical) {
                Add-Type -AssemblyName System.Windows.Forms
                $picker = [System.Windows.Forms.FolderBrowserDialog]::new()
                $picker.Description = '请选择 StarCraft II 安装目录（包含 SC2Data 和 Versions）'
                $picker.ShowNewFolderButton = $false
                try {
                    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw '启动已取消。' }
                    $candidate = $picker.SelectedPath
                } finally { $picker.Dispose() }
            } else { $candidate = Read-Host '请输入 StarCraft II 安装目录（包含 SC2Data 和 Versions 的文件夹）' }
            if ([string]::IsNullOrWhiteSpace($candidate)) { throw '未提供 SC2 路径，启动已取消。' }
        }
        $candidate = $candidate.Trim()
        if ($candidate.Length -ge 2 -and (($candidate.StartsWith('"') -and $candidate.EndsWith('"')) -or
            ($candidate.StartsWith("'") -and $candidate.EndsWith("'")))) {
            $candidate = $candidate.Substring(1, $candidate.Length - 2)
        }
        $engine = @()
        try {
            if ([IO.Path]::IsPathRooted($candidate) -and
                (Test-Path -LiteralPath (Join-Path $candidate 'SC2Data') -PathType Container) -and
                (Test-Path -LiteralPath (Join-Path $candidate 'Versions') -PathType Container)) {
                $engine = @(Get-ChildItem -LiteralPath (Join-Path $candidate 'Versions') -Directory |
                    Where-Object { $_.Name -match '^Base[0-9]+$' -and
                        (Test-Path -LiteralPath (Join-Path $_.FullName 'SC2_x64.exe') -PathType Leaf) })
            }
        } catch { $engine = @() }
        if ($engine.Count -gt 0) { break }
        if ($Graphical) {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show('这个目录不是有效的 Windows SC2 安装目录，请重新选择。', '游戏目录无效') | Out-Null
        } else { Write-Host '这个目录不是有效的 Windows SC2 安装目录，请重新输入。' }
        $candidate = $null
    }
    $env:SC2PATH = (Resolve-Path -LiteralPath $candidate).ProviderPath
    $entry = 'SC2PATH="' + $env:SC2PATH + '"'
    $updated = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*SC2PATH\s*=') {
            if (-not $found) { $updated.Add($entry); $found = $true }
        } else { $updated.Add($line) }
    }
    if (-not $found) { $updated.Add($entry) }
    if (($lines -join "`n") -cne ($updated -join "`n")) {
        [IO.File]::WriteAllLines($envFile, $updated, [Text.UTF8Encoding]::new($false))
    }
    Write-Host "SC2 路径：$env:SC2PATH"
}
