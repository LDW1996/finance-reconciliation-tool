@echo off
setlocal
cd /d "%~dp0\.."

echo [1/5] Checking Python...
py -3 --version || goto :error

echo [2/5] Creating virtual environment...
if not exist .venv-win py -3 -m venv .venv-win
call .venv-win\Scripts\activate.bat || goto :error

echo [3/5] Installing Python dependencies...
python -m pip install --upgrade pip || goto :error
pip install -r backend\requirements.txt pyinstaller==6.11.1 || goto :error

echo [4/5] Checking frontend build...
if not exist frontend\dist\index.html (
  echo frontend\dist not found. Please run npm install --prefix frontend and npm run frontend:build first.
  goto :error
)

echo [5/5] Building exe...
pyinstaller --clean --noconfirm packaging\finance-reconcile.spec || goto :error

echo.
echo Build complete:
echo dist\财务对账工具.exe
echo.
pause
exit /b 0

:error
echo.
echo Build failed. Please check the error above.
pause
exit /b 1
