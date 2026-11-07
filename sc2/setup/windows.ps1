$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.deps/python-windows'
$environment = Join-Path $projectRoot '.deps/sc2-win'
if (-not (Test-Path -LiteralPath (Join-Path $environment 'Scripts/python.exe'))) {
    & uv venv $environment --python 3.10
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Python 3.10 environment.' }
}
& uv pip install --python (Join-Path $environment 'Scripts/python.exe') -r (Join-Path $projectRoot 'sc2/requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned SC2 dependencies.' }
