param([Parameter(Mandatory = $true)][string]$DropDir)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path $DropDir | Out-Null
[Environment]::SetEnvironmentVariable("JOB_SOURCE_DROP_DIR", $DropDir, "User")
$env:JOB_SOURCE_DROP_DIR = $DropDir
python -m pip install --upgrade kabi-boss-cli playwright
python -m playwright install chromium
Write-Host "Authenticate BOSS on this computer. Credentials remain local." -ForegroundColor Cyan
boss login
Write-Host "A dedicated Liepin browser profile will open. Log in normally, then return to the terminal." -ForegroundColor Cyan
$profile = Join-Path $HOME ".job-source-node\liepin-profile"
$env:LIEPIN_PROFILE_DIR = $profile
python -c "from pathlib import Path; from playwright.sync_api import sync_playwright; import os; p=Path(os.environ['LIEPIN_PROFILE_DIR']); p.mkdir(parents=True,exist_ok=True); pw=sync_playwright().start(); c=pw.chromium.launch_persistent_context(user_data_dir=str(p),headless=False,viewport={'width':1440,'height':1000},locale='zh-CN'); page=c.pages[0] if c.pages else c.new_page(); page.goto('https://www.liepin.com/',wait_until='domcontentloaded',timeout=60000); input('Complete Liepin login in the opened browser, then press Enter here: '); c.close(); pw.stop()"
python ..\validation\source_control_plane.py health --context local
Write-Host "Local node setup completed. Install the 07:15 scheduled task next." -ForegroundColor Green
