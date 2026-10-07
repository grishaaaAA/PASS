"""
Рабочее место диспетчера (Б6): страница + API движка в одном процессе.

    python -m naryad.web.dispatcher            # http://127.0.0.1:8101

Страница - naryad/web/dispatcher/app (вариант «Карта»; вариант «Пульт» - в archive/).

Главный экран - парки (/x/parks). Диспетчер открывает парк, получает реестр на утро дня
(/x/registry/<парк>?date=ГГГГ-ММ-ДД), отмечает, что сегодня идёт в работу, и отправляет
день в API движка /api/... (naryad/web/api.py, docs/CONTRACT.md) - план, объяснения, события.
/x/geo - справочник маршрутов на карте (naryad/geo/store.py): GET всё, POST /x/geo/routes
добавить, PUT /x/geo/routes/<id> изменить, DELETE /x/geo/routes/<id> удалить. Редактор - /routes.
/x/marks - отметки диспетчера по парку и дню (naryad/web/marks.py, SQLite data/marks.sqlite):
GET ?parks=P07&date=ГГГГ-ММ-ДД, POST {date, items: [{kind, id, value}]}, GET /x/marks/journal - журнал.
На диск пишет справочник маршрутов data/geo/routes.json и базу отметок data/marks.sqlite.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import webbrowser
from datetime import date as Date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from naryad.data.generate import generate
from naryad.geo.store import GeoError, RouteStore
from naryad.web.marks import MarksError, MarksStore
from naryad.web.api import Engine, allowed_origin

WEB = Path(__file__).resolve().parent
MAX_BODY = 64 * 1024 * 1024


# Доступные парки: реестр есть пока только у парка №7 (справка перевозчика), остальные заблокированы.
REGISTRY_PRESETS = {"P07": "park7"}
# Маршруты, которые в работу диспетчера не берём: сезонные и кладбищенские (2М, 3М, 56Х, 114Х -
# по справке без нарядов) и экспресс 400Э. Справка в генераторе не меняется - их убирает реестр.
EXCLUDED_ROUTES = {"P07": {"2М,2МА,2МБ", "3М,3МА,3МБ", "56Х", "114Х", "400Э"}}


def _exclude_routes(data: dict, park_id: str) -> dict:
    """Убрать маршруты из дня вместе с их нарядами и сменами; выпуск парка уменьшается на эти наряды."""
    dropped = [r for r in data["routes"] if r["park_id"] == park_id and r["number"] in EXCLUDED_ROUTES.get(park_id, ())]
    if not dropped:
        return data
    drop = {r["id"] for r in dropped}
    gone = {d["id"] for d in data["duties"] if d["route_id"] in drop}
    data["routes"] = [r for r in data["routes"] if r["id"] not in drop]
    data["duties"] = [d for d in data["duties"] if d["id"] not in gone]
    data["shifts"] = [s for s in data["shifts"] if s["duty_id"] not in gone]
    for v in data["vehicles"]:
        if v["home_route_id"] in drop:
            v["home_route_id"] = None
    for p in data["parks"]:
        if p["id"] == park_id:
            for kind in ("weekday", "weekend"):
                p[f"release_{kind}"] -= sum(r[f"duties_{kind}"] for r in dropped)
    return data
    gone = {d["id"] for d in data["duties"] if d["route_id"] in drop}
    data["routes"] = [r for r in data["routes"] if r["id"] not in drop]
    data["duties"] = [d for d in data["duties"] if d["id"] not in gone]
    data["shifts"] = [s for s in data["shifts"] if s["duty_id"] not in gone]
    for v in data["vehicles"]:
        if v["home_route_id"] in drop:
            v["home_route_id"] = None
    for p in data["parks"]:
        if p["id"] == park_id:
            for kind in ("weekday", "weekend"):
                p[f"release_{kind}"] -= sum(1 for d in generate_duties_cache(data) if False)  # заглушка не нужна
    return data


def parks_list() -> list:
    """Все парки города для главного экрана. Справочные данные - из вводных кейса.
    kind - вид транспорта: пока только автобусные (bus); троллейбусные (trolley) и трамвайные (tram)
    парки появятся здесь же, когда будут их реестры."""
    city = generate("case")
    out = []
    for p in city["parks"]:
        data = _exclude_routes(generate(REGISTRY_PRESETS[p["id"]]), p["id"]) if p["id"] in REGISTRY_PRESETS else city
        own = [x for x in data["parks"] if x["id"] == p["id"]][0]
        out.append({"id": p["id"], "kind": "bus", "name": p["name"], "address": p["address"], "available": p["id"] in REGISTRY_PRESETS,
                    "routes": sum(r["park_id"] == p["id"] for r in data["routes"]), "vehicles": own["list_count"],
                    "release_weekday": own["release_weekday"], "release_weekend": own["release_weekend"]})
    return out


def registry(park_id: str, day: str, seed: int = 1) -> dict:
    """Реестр парка на утро дня: маршруты, наряды, автобусы с состоянием, водители с графиком и медосмотром.
    Пока это генератор по справке парка; когда перевозчик пришлёт реестры - здесь будет их чтение."""
    preset = REGISTRY_PRESETS.get(park_id)
    if not preset:
        raise KeyError(park_id)
    Date.fromisoformat(day)
    data = _exclude_routes(generate(preset, day=day, seed=seed, moment="morning"), park_id)
    data["meta"]["source"] = f"реестр парка {park_id} на утро {day}"
    return data


class Handler(BaseHTTPRequestHandler):
    engine = Engine()
    geo = RouteStore()
    marks = MarksStore()

    def log_message(self, fmt, *args):
        if "/api/" in self.path or "/x/" in self.path:
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def _send(self, status, body: bytes, ctype: str):
        self.send_response(int(status))
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = allowed_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        if path in ("/routes", "/routes/"):
            path = "/common/routes.html"
        parts = path.lstrip("/").split("/")
        if parts[0] == "ds":
            base, rest = WEB / "static" / "ds", parts[1:]
        elif parts[0] == "common":
            base, rest = WEB / "dispatcher" / "common", parts[1:]
        else:
            base, rest = WEB / "dispatcher" / "app", parts
        target = (base / "/".join(rest)).resolve()
        if base.resolve() not in target.parents or not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого файла"})
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            return self._json(*self.engine.handle("GET", url.path, query))
        if url.path in ("/x/marks", "/x/marks/journal"):
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            parks = [p for p in (q.get("parks") or "").split(",") if p]
            try:
                if url.path == "/x/marks":
                    return self._json(HTTPStatus.OK, self.marks.get(parks, q.get("date") or Date.today().isoformat()))
                return self._json(HTTPStatus.OK, {"journal": self.marks.journal(parks, q.get("date") or Date.today().isoformat())})
            except MarksError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        if url.path == "/x/geo":
            return self._json(HTTPStatus.OK, self.geo.all())
        if url.path == "/x/parks":
            if not hasattr(Handler, "_parks"):
                Handler._parks = parks_list()
            return self._json(HTTPStatus.OK, {"parks": Handler._parks})
        if url.path.startswith("/x/registry/"):
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                return self._json(HTTPStatus.OK, registry(url.path.rsplit("/", 1)[-1], q.get("date") or Date.today().isoformat()))
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "реестр этого парка пока не подключён"})
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "дата - в виде ГГГГ-ММ-ДД"})
        self._static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "файл больше 64 МБ"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "тело запроса: неверный JSON"})
        if url.path == "/x/marks":
            try:
                return self._json(HTTPStatus.OK, self.marks.set(body.get("date") or Date.today().isoformat(), body.get("items"),
                                                                who=self.client_address[0]))
            except MarksError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        if url.path == "/x/geo/routes":
            return self._geo(lambda: self.geo.create(body))
        if url.path.startswith("/api/"):
            return self._json(*self.engine.handle("POST", url.path, {}, body))
        self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого адреса"})

    def _geo(self, action):
        try:
            return self._json(HTTPStatus.OK, action())
        except GeoError as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except KeyError:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого маршрута"})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("слишком большой запрос")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_PUT(self):
        url = urlparse(self.path)
        if not url.path.startswith("/x/geo/routes/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого адреса"})
        try:
            body = self._body()
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "тело запроса: неверный JSON"})
        rid = url.path.rsplit("/", 1)[-1]
        self._geo(lambda: self.geo.update(rid, body))

    def do_DELETE(self):
        url = urlparse(self.path)
        if not url.path.startswith("/x/geo/routes/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "нет такого адреса"})
        rid = url.path.rsplit("/", 1)[-1]
        self._geo(lambda: self.geo.delete(rid))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AUTODISP: рабочее место диспетчера (прототип)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"AUTODISP, рабочее место диспетчера: {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
