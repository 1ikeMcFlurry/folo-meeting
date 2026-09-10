@echo off
REM Build the 8-station factory test tool into a single exe (run on Windows).
REM Output: dist\factory_gui.exe
cd /d "%~dp0\.."
echo === install deps ===
py -m pip install --upgrade pyinstaller pyserial
echo === packaging ===
py -m PyInstaller --clean --noconfirm tools\factory_gui.spec
if errorlevel 1 (
  echo BUILD FAILED - see errors above.
  pause
  exit /b 1
)
echo.
echo DONE: dist\factory_gui.exe
echo factory_test_log.csv will be written next to the exe.
pause
