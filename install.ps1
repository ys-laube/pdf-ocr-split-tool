#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# ============================================================
#   PDF OCR Tool - Windows Installer (winget-based)
# ============================================================
# Edit this single line with your GitHub repo URL after pushing:
$GitHubZipUrl = "https://github.com/YOUR_USERNAME/YOUR_REPO/archive/refs/heads/main.zip"

$InstallDir = Join-Path $env:USERPROFILE "PDF-OCR-Tool"
$TessdataKorUrl = "https://github.com/tesseract-ocr/tessdata_best/raw/main/kor.traineddata"

function Write-Step($n, $msg) { Write-Host ""; Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    [X] $msg" -ForegroundColor Red }

function Refresh-Path {
    $env:Path = ([System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                 [System.Environment]::GetEnvironmentVariable("Path","User"))
}

function Has-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

function Winget-Install($pkgId, $displayName) {
    Write-Host "    Installing $displayName via winget ($pkgId) ..."
    $args = @("install","-e","--id",$pkgId,
              "--accept-source-agreements","--accept-package-agreements",
              "--silent","--disable-interactivity")
    & winget @args 2>&1 | Out-Null
    $code = $LASTEXITCODE
    Refresh-Path
    if ($code -eq 0 -or $code -eq -1978335189) {
        Write-Ok "$displayName ready"
    } else {
        Write-Warn "winget exit code $code (may already be installed; continuing)"
    }
}

Clear-Host
Write-Host "============================================================"
Write-Host "  PDF OCR Tool - Windows Installer"
Write-Host "============================================================"
Write-Host ""
Write-Host "Install location: $InstallDir"
Write-Host ""

Write-Step "1/7" "Checking winget"
if (-not (Has-Command "winget")) {
    Write-Fail "winget not found."
    Write-Host "    Install 'App Installer' from Microsoft Store first:"
    Write-Host "    https://apps.microsoft.com/detail/9NBLGGH4NNS1"
    Read-Host "    Press Enter to exit"
    exit 1
}
Write-Ok "winget available"

Write-Step "2/7" "Installing Python 3.12"
if (Has-Command "python") {
    Write-Ok ("Python already installed: " + (python --version))
} else {
    Winget-Install "Python.Python.3.12" "Python 3.12"
}

Write-Step "3/7" "Installing Tesseract OCR"
if (Has-Command "tesseract") {
    Write-Ok ("Tesseract already installed: " + ((tesseract --version 2>&1 | Select-Object -First 1)))
} else {
    Winget-Install "UB-Mannheim.TesseractOCR" "Tesseract OCR"
}

Write-Step "4/7" "Installing Korean traineddata"
$tessdataCandidates = @(
    "${env:ProgramFiles}\Tesseract-OCR\tessdata",
    "${env:ProgramFiles(x86)}\Tesseract-OCR\tessdata",
    "${env:LOCALAPPDATA}\Programs\Tesseract-OCR\tessdata"
)
$tessdataDir = $null
foreach ($d in $tessdataCandidates) {
    if (Test-Path $d) { $tessdataDir = $d; break }
}
if ($null -eq $tessdataDir) {
    Write-Warn "Tesseract tessdata dir not found. Skip Korean download."
} else {
    $kor = Join-Path $tessdataDir "kor.traineddata"
    if (Test-Path $kor) {
        Write-Ok "Korean traineddata already present"
    } else {
        Write-Host "    Downloading kor.traineddata (~16 MB)..."
        try {
            Invoke-WebRequest -Uri $TessdataKorUrl -OutFile $kor -UseBasicParsing
            Write-Ok "Korean traineddata installed"
        } catch {
            Write-Warn "Download failed. Run installer again with admin rights, or download manually."
            Write-Warn "URL: $TessdataKorUrl  ->  $kor"
        }
    }
}

Write-Step "5/7" "Installing Ghostscript"
$gsKnown = @("gswin64c","gswin32c")
$hasGs = $false
foreach ($g in $gsKnown) { if (Has-Command $g) { $hasGs = $true; break } }
if ($hasGs) {
    Write-Ok "Ghostscript already installed"
} else {
    Winget-Install "ArtifexSoftware.GhostScript" "Ghostscript"
}

Write-Step "6/7" "Downloading PDF OCR Tool code from GitHub"
if (Test-Path $InstallDir) {
    Write-Warn "$InstallDir already exists -- assuming files in place (skipping download)."
} else {
    $tempZip = Join-Path $env:TEMP "pdf-ocr-tool.zip"
    $tempEx = Join-Path $env:TEMP "pdf-ocr-tool-extract"
    if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
    if (Test-Path $tempEx) { Remove-Item $tempEx -Recurse -Force }
    Write-Host "    Downloading $GitHubZipUrl ..."
    try {
        Invoke-WebRequest -Uri $GitHubZipUrl -OutFile $tempZip -UseBasicParsing
        Expand-Archive -Path $tempZip -DestinationPath $tempEx -Force
        $sub = Get-ChildItem -Path $tempEx -Directory | Select-Object -First 1
        Move-Item -Path $sub.FullName -Destination $InstallDir
        Remove-Item $tempZip -Force
        Remove-Item $tempEx -Recurse -Force
        Write-Ok "Code placed at $InstallDir"
    } catch {
        Write-Fail "Failed to download or extract code:"
        Write-Host "    $($_.Exception.Message)"
        Write-Host "    Did you set the correct GitHubZipUrl in this script?"
        Read-Host "    Press Enter to exit"
        exit 1
    }
}

Write-Step "7/7" "Installing Python packages"
Push-Location $InstallDir
try {
    python -m pip install --upgrade pip 2>&1 | Out-Null
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed."
        Read-Host "    Press Enter to exit"
        exit 1
    }
    python -c "import ocrmypdf, fitz, tkinterdnd2; print('imports OK')"
    Write-Ok "Python packages ready"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Creating desktop shortcut..."
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "PDF OCR Tool.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($desktopLnk)
$sc.TargetPath = Join-Path $InstallDir "run.bat"
$sc.WorkingDirectory = $InstallDir
$sc.Description = "PDF OCR Tool"
$sc.Save()
Write-Ok "Desktop shortcut: $desktopLnk"

Write-Host ""
Write-Host "============================================================"
Write-Host " Installation complete!" -ForegroundColor Green
Write-Host " Double-click 'PDF OCR Tool' on your desktop to launch."
Write-Host "============================================================"
Write-Host ""
Read-Host "Press Enter to exit"
