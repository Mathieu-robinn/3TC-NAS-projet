@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo [ERR] python introuvable dans le PATH.
  exit /b 1
)

if /i "%~1"=="--venv" (
  echo [INFO] Creation du venv .venv ...
  python -m venv .venv
  if errorlevel 1 exit /b 1
  call ".venv\Scripts\activate.bat"
)

echo [INFO] Mise a jour pip ...
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [INFO] Installation depuis requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [OK] Termine. Si --venv : le venv est active dans cette fenetre.
endlocal
