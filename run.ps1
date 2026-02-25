# Run Bulk Email Verifier (Streamlit)
# Use from this folder: .\run.ps1
# Or from workspace root: .\bulk-email-verifier\run.ps1
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
if (-not (Test-Path "requirements.txt")) { Write-Error "requirements.txt not found. Run from bulk-email-verifier folder."; exit 1 }
Write-Host "Installing dependencies if needed..."
pip install -r requirements.txt -q
Write-Host "Starting Streamlit at http://localhost:8501"
streamlit run app.py
