@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [Lesson Listener] Создание виртуального окружения Python...
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
echo [Lesson Listener] Установка зависимостей...
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo ========================================================
echo  Установка завершена! Запусти run.bat для старта суфлера
echo ========================================================
pause
