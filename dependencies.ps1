# Installe les dependances Python (Windows PowerShell).
# Usage: .\dependencies.ps1
#        .\dependencies.ps1 -Venv   # cree et active .venv dans la session
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "python introuvable dans le PATH."
}

if ($args -contains "-Venv" -or $args -contains "--venv") {
    Write-Host "[INFO] Creation du venv .venv ..."
    & python -m venv .venv
    & "$PSScriptRoot\.venv\Scripts\Activate.ps1"
}

Write-Host "[INFO] Mise a jour pip ..."
& python -m pip install --upgrade pip

Write-Host "[INFO] Installation depuis requirements.txt ..."
& python -m pip install -r requirements.txt

Write-Host "[OK] Termine."
