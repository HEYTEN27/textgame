@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo === 1. Виртуальное окружение и зависимости ===
if not exist .venv (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip > nul
python -m pip install -r requirements.txt || goto :fail

echo === 2. Тесты ===
python -m unittest discover -s tests || goto :fail

echo === 3. Валидатор сценариев ===
python tools\validate.py || goto :fail

echo === 4. Бот-тестировщик ===
python tools\autoplay.py -n 1000 || goto :fail

echo === 5. Сборка exe ===
pyinstaller --noconfirm --clean --onefile --console --name "TextAdventure" --add-data "data;data" main.py || goto :fail

echo.
echo Готово: dist\TextAdventure.exe
echo Сохранения будут создаваться в папке saves рядом с exe.
exit /b 0

:fail
echo.
echo СБОРКА ПРЕРВАНА: один из шагов завершился с ошибкой.
exit /b 1
