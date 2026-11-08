$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.deps/python-windows'
$environment = Join-Path $projectRoot '.deps/sc2-win'
$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
$uv = Join-Path $projectRoot '.deps/uv/uv.exe'
if ($uvCommand) { $uv = $uvCommand.Source }
if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
    Write-Host '首次运行：正在下载 uv 环境工具...'
    $installer = Join-Path $projectRoot '.cache/uv-install.ps1'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $installer) | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.12.8/install.ps1' -OutFile $installer -TimeoutSec 120
    $previousInstall = $env:UV_UNMANAGED_INSTALL
    try {
        $env:UV_UNMANAGED_INSTALL = Split-Path -Parent $uv
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'uv 安装失败，请检查网络后重试。' }
    } finally { $env:UV_UNMANAGED_INSTALL = $previousInstall }
}
if (-not (Test-Path -LiteralPath (Join-Path $environment 'Scripts/python.exe'))) {
    & $uv venv $environment --python 3.10 --managed-python
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Python 3.10 environment.' }
}
& $uv pip install --python (Join-Path $environment 'Scripts/python.exe') -r (Join-Path $projectRoot 'sc2/requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned SC2 dependencies.' }
