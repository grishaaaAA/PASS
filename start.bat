@echo off
chcp 65001 >nul
title AUTODISP - генератор данных
cd /d "%~dp0"
where git >nul 2>nul && git pull --ff-only
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m naryad.web.server
) else (
  python -m naryad.web.server
)
echo.
echo Сервер остановлен. Если выше ошибка - пришлите её текст.
pause
