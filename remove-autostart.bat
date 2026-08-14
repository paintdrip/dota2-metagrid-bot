@echo off
rem Удаление dota2-metagrid из автозагрузки Windows.
rem Положите этот файл рядом с dota2-metagrid.exe и запустите двойным кликом.

cd /d "%~dp0"
if not exist dota2-metagrid.exe (
    echo Ошибка: dota2-metagrid.exe не найден рядом с этим файлом.
    pause
    exit /b 1
)

dota2-metagrid.exe --uninstall
echo.
echo Если выше написано, что задачи удалены, — автозагрузка отключена.
echo Теперь dota2-metagrid.exe можно просто удалить.
pause
