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
echo [3/3] Compressing portable ZIP...
powershell -Command "Compress-Archive -Force -Path dist\Speakaloud\* -DestinationPath dist\Speakaloud-Portable-Windows-ARM64.zip"

echo.
echo ========================================================
echo   SUCCESS!
echo   Standalone folder:  dist\Speakaloud\Speakaloud.exe
echo   Portable ZIP:       dist\Speakaloud-Portable-Windows-ARM64.zip
echo ========================================================
pause
