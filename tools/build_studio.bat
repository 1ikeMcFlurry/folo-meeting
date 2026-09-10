@echo off
REM Build TRAE-CARD toolbox into a single exe (run on Windows).
REM Output: dist\trae_studio.exe
cd /d "%~dp0\.."
echo === install build + runtime deps ===
py -m pip install --upgrade pyinstaller pyserial
REM Optional (audio / image / BLE panels; install if you need them):
REM py -m pip install librosa soundfile numpy miniaudio pillow bleak

echo === packaging ===
py -m PyInstaller --clean --noconfirm tools\trae_studio.spec
if errorlevel 1 (
  echo BUILD FAILED - see errors above.
  pause
  exit /b 1
)
echo.
echo DONE: dist\trae_studio.exe
echo Put cardid_ledger.csv next to the exe so identity/token panels can read it.
pause
