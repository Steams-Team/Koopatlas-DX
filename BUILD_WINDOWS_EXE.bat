@echo off
cd /d "%~dp0"
echo Building Koopatlas DX (needs internet once for PyInstaller and the requirements).
py -3 -m pip install --user pyinstaller -r requirements.txt
if errorlevel 1 (echo Installation failed.& pause & exit /b 1)
py -3 -m PyInstaller --noconfirm --clean --windowed --name KoopatlasDX --hidden-import OpenGL.platform.win32 --hidden-import OpenGL.arrays.numpymodule --collect-submodules kdx --add-binary "kdx\fastyz\fastyz_win64.dll;kdx\fastyz" koopatlasdx.py
if errorlevel 1 (echo Build failed.& pause & exit /b 1)
echo Output: dist\KoopatlasDX\KoopatlasDX.exe
pause
