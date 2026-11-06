@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --name CryptoRobot app.py
echo.
echo Готово: dist\CryptoRobot.exe
pause
