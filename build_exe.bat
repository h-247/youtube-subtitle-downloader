@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "RELEASE_DIR=%PROJECT_DIR%.."

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "YouTubeSubtitleDownloader" ^
  --icon "%PROJECT_DIR%assets\app-icon.ico" ^
  --add-data "%PROJECT_DIR%assets\app-icon.ico;assets" ^
  --collect-all customtkinter ^
  --collect-all pystray ^
  --distpath "%RELEASE_DIR%" ^
  --workpath "%PROJECT_DIR%build\pyinstaller" ^
  --specpath "%PROJECT_DIR%build\spec" ^
  "%PROJECT_DIR%gui.py"

endlocal
