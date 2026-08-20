@echo off
chcp 65001 >nul
title Установка зависимостей парсера

echo ============================================
echo   Установка зависимостей парсера Я.Карт
echo ============================================
echo.

echo [1/4] Проверка Python...
python --version
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Python не найден в PATH.
    echo Скачай Python: https://www.python.org/downloads/
    echo При установке поставь галку "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
echo.

echo [2/4] Обновление pip...
python -m pip install --upgrade pip
echo.

echo [3/4] Установка библиотек (customtkinter, playwright, openpyxl)...
python -m pip install customtkinter playwright openpyxl
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить библиотеки.
    pause
    exit /b 1
)
echo.

echo [4/4] Установка браузера Chromium для Playwright...
echo Это займёт 2-3 минуты, скачается ~150 МБ.
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось скачать Chromium.
    pause
    exit /b 1
)
echo.

echo ============================================
echo   УСПЕХ! Всё установлено.
echo   Запусти приложение через run.bat
echo ============================================
echo.
pause
