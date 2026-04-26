@echo off
REM ==================================================================
REM Запускает мониторинг FunPay + PayGame и открывает report.md
REM в дефолтном приложении (у тебя — Antigravity).
REM
REM Положи этот файл (или его ярлык) на рабочий стол. Двойной клик —
REM скрипт сходит на оба сайта, обновит scripts\report.md и откроет
REM его. Если что-то упало — окно НЕ закроется автоматически, чтобы
REM можно было прочитать текст ошибки.
REM ==================================================================
setlocal enableextensions enabledelayedexpansion
chcp 65001 >nul

REM Перейти в каталог, где лежит этот .bat (это корень репо).
cd /d "%~dp0"

REM Найти Python: предпочитаем launcher py.exe (стандартный для Win),
REM иначе python из PATH.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [run_monitor] не нашёл python ^(ни py.exe, ни python.exe^).
    echo Установи Python 3 с https://www.python.org/downloads/ и поставь галку "Add to PATH".
    pause
    exit /b 1
)

echo [run_monitor] %DATE% %TIME% — стартую мониторинг...
%PY% scripts\monitor_all.py --open
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [run_monitor] скрипт вернул код %RC% ^(0 = всё ок, 2 = один из источников упал^).
    echo Подробности — выше в логе. Окно оставлено открытым.
    pause
)

endlocal
exit /b %RC%
