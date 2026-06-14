@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM Try pythonw (no console), then python (with console) as fallback.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw pdf_ocr_gui.py
    goto :eof
)

where python >nul 2>&1
if %errorlevel%==0 (
    python pdf_ocr_gui.py
    if errorlevel 1 (
        echo.
        echo [ERROR] The program exited with an error. See the message above.
        pause
    )
    goto :eof
)

echo.
echo [ERROR] Neither pythonw nor python was found in PATH.
echo Install Python from https://www.python.org/ and try again.
pause