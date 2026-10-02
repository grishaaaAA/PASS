#!/bin/bash
# AUTODISP - генератор данных. Двойной клик в Finder запускает сервер и открывает браузер.
cd "$(dirname "$0")"
command -v git >/dev/null && git pull --ff-only
python3 -m naryad.web.server
echo
echo "Сервер остановлен. Если выше ошибка - пришлите её текст."
read -r -p "Нажмите Enter, чтобы закрыть окно"
