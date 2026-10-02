"""
Сервер генератора: страница с парками, данные на один день и выгрузка.

Сценарий диспетчера: пришёл утром, отметил парки, при необходимости поправил
их вводные, нажал «Сгенерировать» - получил данные на сегодня (весь рабочий
день: от первого выезда до последнего возврата) и выгрузил их.

Сервер ничего не хранит: выгрузка заново строится по тем же вводным и
номеру набора и поэтому совпадает с показанной сводкой.

Запуск:
    python -m naryad.web.server            # http://localhost:8000
    python -m naryad.web.server --port 8080
"""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import re
import sys
import tempfile
import zipfile
from datetime import date as Date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..data.check import check
from ..data.city import _expected_reserve, default_setup, resolve, sheet_park
from ..data.csvio import write_csv
from ..data.generate import day_numbers, generate
from ..data.presets import CITY_PARKS, CLASS_LABELS, CLASSES, PARK7

STATIC = Path(__file__).parent / "static"
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
PARK_NAMES = {park_id: name for park_id, name, _ in CITY_PARKS}


def humanize(text: str) -> str:
    """Сообщение проверки -> текст для диспетчера: названия парков и классов по-русски."""
    text = re.sub(r"(?:парк )?(P\d\d)\b", lambda m: PARK_NAMES.get(m.group(1), m.group(0)), text)
    return re.sub(r"класс (medium|big|extra_big)",
                  lambda m: f"класс «{CLASS_LABELS[m.group(1)]}»", text)


def _sheet_values() -> dict:
    """Что стоит в строке парка №7, если взять цифры из справки (будний день)."""
    spec = sheet_park()
    classes = {cls: 0 for cls in CLASSES}
    for _, weekday, _, _, cls in spec["routes"]:
        classes[cls] += weekday
    for cls, n in _expected_reserve(spec, "weekday").items():
        classes[cls] += n
    return {"classes": classes, "routes": len(spec["routes"]),
            "readiness": round(spec["tech_readiness"] * 100, 1)}


def defaults() -> dict:
    sheet = _sheet_values()
    setup = {entry["id"]: entry for entry in default_setup(resolve("case"))}
    parks = []
    for park_id, name, address in CITY_PARKS:
        entry = setup[park_id]
        parks.append({
            "id": park_id, "name": name, "address": address,
            "classes": entry["classes"], "routes": entry["routes"],
            "readiness": round(entry["readiness"] * 100, 1),
            "sheet": sheet if park_id == PARK7["id"] else None,
        })
    return {"parks": parks, "classes": {cls: CLASS_LABELS[cls] for cls in CLASSES}}


def parse_request(raw: dict) -> dict:
    """Запрос страницы -> (дата, номер набора, настройки парков). Ошибки - понятным текстом."""
    def number(value, kind, low, high, label):
        try:
            value = kind(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: нужно число") from None
        if not low <= value <= high:
            raise ValueError(f"{label}: от {low} до {high}")
        return value

    try:
        day = Date.fromisoformat(str(raw.get("date") or ""))
    except ValueError:
        raise ValueError("Дата: укажите день") from None
    seed = number(raw.get("seed", 1), int, 0, 10**9, "Номер набора")
    setup = []
    for item in raw.get("parks") or []:
        park_id = item.get("id")
        if park_id not in PARK_NAMES:
            raise ValueError(f"Нет парка {park_id}")
        if not item.get("enabled"):
            continue
        name = PARK_NAMES[park_id]
        if item.get("sheet"):
            if park_id != PARK7["id"]:
                raise ValueError(f"{name}: справки по этому парку нет")
            setup.append({"id": park_id, "sheet": True})
            continue
        classes = {cls: number((item.get("classes") or {}).get(cls, 0), int, 0, 2000,
                               f"{name}, класс «{CLASS_LABELS[cls]}»") for cls in CLASSES}
        if not sum(classes.values()):
            raise ValueError(f"{name}: укажите выпуск хотя бы по одному классу")
        setup.append({
            "id": park_id, "sheet": False, "classes": classes,
            "routes": number(item.get("routes"), int, 1, 300, f"{name}, маршрутов"),
            "readiness": number(item.get("readiness"), float, 50, 100, f"{name}, исправных %") / 100,
        })
    if not setup:
        raise ValueError("Отметьте хотя бы один парк")
    return {"day": day.isoformat(), "seed": seed, "setup": setup}


def build(request: dict) -> dict:
    return generate("case", request["day"], request["seed"], "morning",
                    parks_setup=request["setup"])


def summarize(request: dict) -> dict:
    data = build(request)
    report = check(data)
    issues: dict = {}
    for text in report["warnings"] + report["errors"]:
        found = re.search(r"\b(P\d\d)\b", text)
        issues.setdefault(found.group(1) if found else "", []).append(humanize(text))
    parks = []
    for park in data["parks"]:
        numbers = day_numbers(data, park["id"])
        numbers.update(id=park["id"], routes=sum(r["park_id"] == park["id"] for r in data["routes"]),
                       issues=issues.get(park["id"], []))
        parks.append(numbers)
    day = Date.fromisoformat(request["day"])
    totals = day_numbers(data)
    totals["routes"] = len(data["routes"])
    return {
        "date": request["day"],
        "weekday": WEEKDAYS[day.weekday()],
        "day_type": data["meta"]["day_type"],
        "seed": request["seed"],
        "totals": totals,
        "parks": parks,
        "other_issues": issues.get("", []),
        "error_count": len(report["errors"]),
        "assumptions": data["meta"]["assumptions"],
    }


def export(request: dict, kind: str) -> tuple:
    """Файл для скачивания: (имя, тип, байты)."""
    data = build(request)
    stem = f"autodisp_{request['day']}_seed{request['seed']}"
    if kind == "json":
        return f"{stem}.json", "application/json", \
            json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    if kind == "csv":
        buffer = io.BytesIO()
        with tempfile.TemporaryDirectory() as folder, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            write_csv(data, folder)
            for path in sorted(Path(folder).iterdir()):
                archive.write(path, path.name)
        return f"{stem}_csv.zip", "application/zip", buffer.getvalue()
    raise ValueError("Формат выгрузки: json или csv")


class Handler(BaseHTTPRequestHandler):
    server_version = "AUTODISP"

    def log_message(self, fmt, *args):  # в консоль - только ошибки
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    def _send(self, status, body: bytes, kind: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(raw, dict):
            raise ValueError("ожидался объект JSON")
        return raw

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/defaults":
            return self._json(HTTPStatus.OK, defaults())
        return self._static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            raw = self._body()
            request = parse_request(raw)
            if path == "/api/generate":
                return self._json(HTTPStatus.OK, summarize(request))
            if path == "/api/export":
                name, kind, body = export(request, raw.get("format", "json"))
                return self._send(HTTPStatus.OK, body, kind,
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого адреса"})
        except ValueError as error:  # сюда же попадает неверный JSON
            return self._json(HTTPStatus.BAD_REQUEST, {"error": humanize(str(error))})

    def _static(self, path: str):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / relative).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого файла"})
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind == "application/javascript":
            kind += "; charset=utf-8"
        return self._send(HTTPStatus.OK, target.read_bytes(), kind)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сервер генератора AUTODISP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Генератор: http://{args.host}:{args.port}  (остановить - Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
