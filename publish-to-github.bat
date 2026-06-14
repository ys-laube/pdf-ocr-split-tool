@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ===============================================
echo   PDF OCR Tool - Publish to GitHub
echo ===============================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [X] Git is not installed.
    echo     Download Git for Windows from https://git-scm.com/download/win
    pause
    exit /b 1
)

echo Step 1: Create a NEW PUBLIC repository on GitHub.
echo         Go to https://github.com/new
echo         Repository name suggestion: pdf-ocr-tool
echo         DO NOT initialize with README, .gitignore, or LICENSE.
echo.
echo Step 2: Copy the repo URL once created.
echo         Example: https://github.com/yourname/pdf-ocr-tool.git
echo.
set /p REPO_URL="Paste the repo URL here: "

if "%REPO_URL%"=="" (
    echo [X] No URL given. Aborting.
    pause
    exit /b 1
)

if not exist ".git" (
    echo.
    echo Initializing local git repository...
    git init
    git branch -M main
)

git add .
git -c user.email="you@example.com" -c user.name="PDF OCR Tool Owner" commit -m "Initial release: GUI OCR + merge + split tool" --allow-empty

git remote remove origin 2>nul
git remote add origin "%REPO_URL%"

echo.
echo Pushing to %REPO_URL% ...
git push -u origin main

if errorlevel 1 (
    echo.
    echo [X] Push failed. Common causes:
    echo     - Wrong URL
    echo     - Not logged in -- a browser/GitHub auth window should appear; complete it and rerun this script
    pause
    exit /b 1
)

echo.
echo ===============================================
echo   Push successful!
echo.
echo   IMPORTANT NEXT STEP:
echo   Open install.ps1 in any text editor and edit this line:
echo     $GitHubZipUrl = "https://github.com/YOUR_USERNAME/YOUR_REPO/archive/refs/heads/main.zip"
echo   Replace YOUR_USERNAME/YOUR_REPO with the actual values from your repo URL.
echo   Then run this batch file again to push the fix.
echo.
echo   After that, anyone can install the tool on a new PC with:
echo     irm https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/install.ps1 ^| iex
echo ===============================================
pause