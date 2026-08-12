@echo off
rem Локальная сборка dota2-metagrid.exe под Windows.
rem Требуется Python 3.10+ в PATH.

python -m pip install -r requirements.txt -r requirements-dev.txt || exit /b 1
pyinstaller --onefile --name dota2-metagrid metagrid.py || exit /b 1

echo.
echo Готово: dist\dota2-metagrid.exe
