param([string]$Sources = "boss,liepin")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install --upgrade kabi-boss-cli playwright
python -m playwright install chromium
if (-not $env:JOB_SOURCE_DROP_DIR) {
    $saved = [Environment]::GetEnvironmentVariable("JOB_SOURCE_DROP_DIR", "User")
    if ($saved) { $env:JOB_SOURCE_DROP_DIR = $saved }
    else {
        $defaultDrop = Join-Path $PSScriptRoot "job-source-drop"
        New-Item -ItemType Directory -Force -Path $defaultDrop | Out-Null
        $env:JOB_SOURCE_DROP_DIR = $defaultDrop
    }
}
python ..\validation\source_control_plane.py validate
python ..\validation\source_control_plane.py health --context local
python ..\validation\source_control_plane.py collect-local --config .\config.example.json --sources $Sources
