@echo off
chcp 65001 >nul
title Сборка .exe для GitHub Releases

echo ============================================
echo   Сборка Парсер_ЯКарт.exe
echo ============================================
echo.

echo [1/3] Установка PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить PyInstaller.
    pause
    exit /b 1
)
echo.

echo [2/3] Проверка наличия Chromium (Playwright)...
if not exist "%LOCALAPPDATA%\ms-playwright" (
    echo Chromium не найден, устанавливаю...
    python -m playwright install chromium
)
echo.

echo [3/3] Сборка .exe (это займёт 3-5 минут)...
echo.
python -m PyInstaller --noconfirm --clean ^
    --onefile ^
    --windowed ^
    --name "ParserYandex" ^
    --collect-all customtkinter ^
    --collect-all playwright ^
    --add-data "%LOCALAPPDATA%\ms-playwright;ms-playwright" ^
    main.py

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Сборка не удалась.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   ГОТОВО!
echo   Файл: dist\ParserYandex.exe
echo ============================================
echo.
echo Можешь запустить его двойным кликом для проверки,
echo либо загрузить на GitHub Releases.
echo.
pause
