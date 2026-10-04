"""
Сервер генератора: страница с вводными, сводка и выгрузка данных.

Сервер ничего не хранит: каждый запрос заново строит данные по вводным и
номеру набора, а одинаковые вводные дают одинаковые данные.

Запуск:
    python -m naryad.web.server            # http://localhost:8000
    python -m naryad.web.server --port 8080
"""

from __future__ import annotations

import argparse
import io
import re
import json
import mimetypes
import sys
import tempfile
import zipfile
from datetime import date as Date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..data.check import check
from ..data.csvio import write_csv
from ..data.generate import SERIES_COLUMNS, day_numbers, generate_series
from ..data.presets import CASE, CLASS_LABELS, CLASSES

STATIC = Path(__file__).parent / "static"
MAX_DAYS = 60
MAX_WARNINGS = 50


def parse_inputs(raw: dict) -> dict:
    """Вводные со страницы -> параметры generate_series. Ошибки - понятным текстом."""
    def number(key, kind, low, high, label):
        value = raw.get(key)
        if value in (None, ""):
            return None
        try:
            value = kind(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: нужно число") from None
        if not low <= value <= high:
            raise ValueError(f"{label}: от {low} до {high}")
        return value

    preset = raw.get("preset", "case")
    if preset not in ("case", "park7"):
        raise ValueError("Настройка: case или park7")
    start = raw.get("start") or "2026-10-05"
    try:
        Date.fromisoformat(start)
    except ValueError:
        raise ValueError("Дата начала: в виде ГГГГ-ММ-ДД") from None
    moment = raw.get("moment") or "morning"
    if moment not in ("morning", "plan"):
        raise ValueError("Момент: morning или plan")
    params = {
        "preset": preset,
        "start": start,
        "days": number("days", int, 1, MAX_DAYS, "Дней") or 1,
        "seed": number("seed", int, 0, 10**9, "Номер набора") or 0,
        "moment": moment,
    }
    if preset == "case":
        inputs = {
            "park_count": number("park_count", int, 1, 7, "Парков"),
            "release_per_park": number("release_per_park", int, 10, 1000, "Выпуск парка"),
            "routes_total": number("routes_total", int, 1, 500, "Маршрутов"),
            "tech_readiness": number("tech_readiness", float, 0.5, 1.0, "Тех. готовность"),
            "weekend_factor": number("weekend_factor", float, 0.3, 1.0, "Выпуск в выходной"),
        }
        mix = {cls: number(f"mix_{cls}", int, 0, 100000, f"Класс «{CLASS_LABELS[cls]}»")
               for cls in CLASSES}
        if any(v is not None for v in mix.values()):
            mix = {cls: v or 0 for cls, v in mix.items()}
            if not sum(mix.values()):
                raise ValueError("Классы: хотя бы один больше нуля")
            inputs["class_mix"] = mix
        params.update({k: v for k, v in inputs.items() if v is not None})
    return params


def humanize(text: str, names: dict) -> str:
    """Сообщение проверки -> текст для диспетчера: названия парков и классов по-русски."""
    text = re.sub(r"парк (P\d+)", lambda m: names.get(m.group(1), m.group(0)), text)
    return re.sub(r"класс (medium|big|extra_big)",
                  lambda m: f"класс «{CLASS_LABELS[m.group(1)]}»", text)


def _series(params: dict):
    params = dict(params)
    return generate_series(params.pop("preset"), params.pop("start"), params.pop("days"),
                           params.pop("seed"), params.pop("moment"), **params)


def summarize(params: dict) -> dict:
    """Сводка для страницы: город, парки первого дня и строка на каждый день."""
    days, first = [], None
    for data in _series(params):
        report = check(data)
        names = {p["id"]: p["name"] for p in data["parks"]}
        row = day_numbers(data)
        row.update(date=data["meta"]["date"], day_type=data["meta"]["day_type"],
                   errors=[humanize(t, names) for t in report["errors"][:MAX_WARNINGS]],
                   warnings=[humanize(t, names) for t in report["warnings"][:MAX_WARNINGS]],
                   error_count=len(report["errors"]), warning_count=len(report["warnings"]))
        days.append(row)
        if first is None:
            first = data
    parks = []
    for park in first["parks"]:
        numbers = day_numbers(first, park["id"])
        numbers.update(id=park["id"], name=park["name"], address=park["address"],
                       routes=sum(r["park_id"] == park["id"] for r in first["routes"]))
        parks.append(numbers)
    meta = first["meta"]
    return {
        "meta": {k: meta[k] for k in ("preset", "inputs", "seed", "date", "moment", "source",
                                      "assumptions", "format_version")},
        "routes": len(first["routes"]),
        "parks": parks,
        "days": days,
    }


def export(params: dict, kind: str) -> tuple:
    """Файл для скачивания: (имя, тип, байты)."""
    stem = f"autodisp_{params['preset']}_{params['start']}_{params['days']}d_seed{params['seed']}"
    if kind == "day_json":
        one = dict(params, days=1)
        data = next(_series(one))
        body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        return f"autodisp_{params['preset']}_{params['start']}_seed{params['seed']}.json", \
            "application/json", body
    if kind == "day_csv":
        data = next(_series(dict(params, days=1)))
        buffer = io.BytesIO()
        with tempfile.TemporaryDirectory() as folder, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            write_csv(data, folder)
            for path in sorted(Path(folder).iterdir()):
                archive.write(path, path.name)
        return f"autodisp_{params['preset']}_{params['start']}_seed{params['seed']}_csv.zip", \
            "application/zip", buffer.getvalue()
    if kind == "series_zip":
        buffer = io.BytesIO()
        rows = []
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for data in _series(params):
                archive.writestr(f"days/{data['meta']['date']}.json",
                                 json.dumps(data, ensure_ascii=False, separators=(",", ":")))
                report = check(data)
                row = day_numbers(data)
                row.update(date=data["meta"]["date"], day_type=data["meta"]["day_type"],
                           errors=len(report["errors"]), warnings=len(report["warnings"]))
                rows.append(row)
            table = io.StringIO()
            table.write(";".join(title for _, title in SERIES_COLUMNS) + "\n")
            for row in rows:
                table.write(";".join(str(row[key]) for key, _ in SERIES_COLUMNS) + "\n")
            archive.writestr("series.csv", "﻿" + table.getvalue())
        return f"{stem}.zip", "application/zip", buffer.getvalue()
    raise ValueError("Формат выгрузки: day_json, day_csv или series_zip")


def defaults() -> dict:
    return {
        "max_days": MAX_DAYS,
        "case": {
            "park_count": CASE["park_count"],
            "release_per_park": CASE["release_per_park"],
            "routes_total": CASE["routes_total"],
            "tech_readiness": CASE["tech_readiness"],
            "weekend_factor": CASE["weekend_factor"],
            "class_mix": CASE["class_mix"],
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AUTODISP"

    def log_message(self, fmt, *args):  # тише в консоли: только ошибки
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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/defaults":
            return self._json(HTTPStatus.OK, defaults())
        if url.path == "/api/export":
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                params = parse_inputs(query)
                name, kind, body = export(params, query.get("format", "series_zip"))
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return self._send(HTTPStatus.OK, body, kind,
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self._static(url.path)

    def do_POST(self):
        if urlparse(self.path).path != "/api/generate":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого адреса"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = json.loads(self.rfile.read(length) or b"{}")
            return self._json(HTTPStatus.OK, summarize(parse_inputs(raw)))
        except ValueError as error:  # сюда же попадает неверный JSON
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _static(self, path: str):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        try:
            target = (STATIC / relative).resolve()
            inside = STATIC.resolve() in target.parents and target.is_file()
        except (ValueError, OSError):
            inside = False  # например нулевой байт в пути: тот же ответ, что на любой плохой путь
        if not inside:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого файла"})
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript",):
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
