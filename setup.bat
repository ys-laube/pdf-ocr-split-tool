@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo === PDF OCR Tool - Setup Check ===
echo.

REM ---- 1) Python ----
where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found in PATH.
    echo     Install Python 3.10+ from https://www.python.org/downloads/
    echo     IMPORTANT: During install, check "Add python.exe to PATH".
    goto :error
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>nul
if errorlevel 1 (
    echo [X] Python version is too old. Need 3.10 or newer.
    python --version
    goto :error
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo [OK] %%v
echo.

REM ---- 2) Tesseract ----
where tesseract >nul 2>&1
if errorlevel 1 (
    echo [X] Tesseract OCR not found in PATH.
    echo     Install from https://github.com/UB-Mannheim/tesseract/wiki
    echo     During install, select Korean ^(kor^) language data.
    echo     Then add the install folder ^(e.g. C:\Program Files\Tesseract-OCR^) to PATH.
    goto :error
)
echo [OK] Tesseract found.

tesseract --list-langs 2>nul | findstr /R /B /C:"kor" >nul
if errorlevel 1 (
    echo [X] Korean language pack ^(kor^) NOT installed for Tesseract.
    echo     Re-run Tesseract installer and add Korean language data.
    goto :error
)
echo [OK] Korean language pack present.

tesseract --list-langs 2>nul | findstr /R /B /C:"eng" >nul
if errorlevel 1 (
    echo [X] English language pack ^(eng^) NOT installed for Tesseract.
    goto :error
)
echo [OK] English language pack present.
echo.

REM ---- 3) Ghostscript ----
where gswin64c >nul 2>&1
if not errorlevel 1 goto :gs_ok
where gswin32c >nul 2>&1
if not errorlevel 1 goto :gs_ok
echo [X] Ghostscript not found in PATH.
echo     Install from https://www.ghostscript.com/releases/gsdnld.html
echo     ^(Windows 64-bit recommended^).
goto :error
:gs_ok
echo [OK] Ghostscript found.
echo.

REM ---- 4) Python packages ----
echo === Installing Python packages from requirements.txt ===
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [X] pip install failed. See error message above.
    goto :error
)
echo.

REM ---- 5) Final import sanity check ----
python -c "import ocrmypdf, fitz, tkinterdnd2; print('[OK] All Python modules import correctly.')"
if errorlevel 1 (
    echo [X] Python packages installed, but importing one of them failed.
    goto :error
)

echo.
echo ============================================
echo  Setup complete! Double-click run.bat to start.
echo ============================================
echo.
pause
goto :eof

:error
echo.
echo === Setup did NOT complete. Fix the issue above and re-run setup.bat. ===
pause
exit /b 1