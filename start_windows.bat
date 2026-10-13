@echo off
REM Double-click to install (first time) and start the Forum Post Extractor.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First start: creating the Python environment...
    python -m venv .venv || (echo Python was not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
)
call ".venv\Scripts\activate.bat"
if not exist ".venv\installed.flag" (
    echo Installing packages, this takes a few minutes the first time...
    python -m pip install --upgrade pip
    pip install -r requirements.txt || (echo Package installation failed. & pause & exit /b 1)
    python -m playwright install chromium || (echo Chromium download failed. & pause & exit /b 1)
    echo ok> ".venv\installed.flag"
)
start "" http://127.0.0.1:8765
python -m forum_extractor serve
pause
