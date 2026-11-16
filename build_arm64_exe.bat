@echo off
echo ========================================================
echo   Speakaloud - Build Standalone ARM64 Executable
echo ========================================================
echo.
cd /d "%~dp0"

echo [1/3] Checking dependencies...
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo.
echo [2/3] Building standalone executable with PyInstaller...
pyinstaller --noconfirm Speakaloud.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed! Check messages above.
    pause
    exit /b %errorlevel%
)

echo.
echo [3/4] Compressing portable ZIP...
powershell -Command "Compress-Archive -Force -Path dist\Speakaloud\* -DestinationPath dist\Speakaloud-Portable-Windows-ARM64.zip"

echo.
echo [4/4] Checking for Inno Setup compiler...
powershell -Command "
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
if (-not $iscc) {
  if (Test-Path \"$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe\") {
    $iscc = \"$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe\"
  } elseif (Test-Path \"C:\Program Files (x86)\Inno Setup 6\ISCC.exe\") {
    $iscc = \"C:\Program Files (x86)\Inno Setup 6\ISCC.exe\"
  }
}
if ($iscc) {
  Write-Host \"Compiling Windows installer with $iscc...\"
  & $iscc /DAppArch=arm64 installer.iss
} else {
  Write-Host 'Inno Setup compiler not found; skipping installer build.'
}
"

echo.
echo ========================================================
echo   SUCCESS!
echo   Standalone folder:  dist\Speakaloud\Speakaloud.exe
echo   Portable ZIP:       dist\Speakaloud-Portable-Windows-ARM64.zip
echo   Installer Setup:    dist-installer\Speakaloud-Setup-arm64.exe
echo ========================================================
pause
