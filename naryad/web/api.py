"""
API движка (Б5): день в памяти под номером, план, объяснения, события.

Форматы - docs/CONTRACT.md, разделы «Состояние дня» и «API движка».
Образцы ответов - data/samples/api_examples.json: их пишет этот же модуль
(--write-examples), а тест сверяет файл с живыми ответами.

Запуск отдельным сервером (пока страница генератора не подключила модуль):
    python -m naryad.web.api                   # http://localhost:8001
    python -m naryad.web.api --labor likely    # набор норм на весь сервер
    python -m naryad.web.api --write-examples  # переписать образцы ответов

Сервер ничего не пишет на диск: дни живут в памяти, перезапуск их стирает.
Объяснения относятся к утреннему плану, состояние дня после событий - отрезками.
Единственная точка входа - Engine.handle(метод, адрес, параметры, тело):
отдаёт код ответа и JSON, сервер страницы генератора может звать её напрямую.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date as Date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from naryad import explain
from naryad.core.invariants import (LABOR_FILE, check_plan, labor_preset_name, labor_presets,
                                    load_labor, use_labor_preset)
from naryad.core.model import Day, Plan
from naryad.data.check import check
from naryad.data.generate import generate
from naryad.ops.replan import Accident, Breakdown, NoShow, OpsState, apply, check_state, options_for
from naryad.solve.drivers import History, solve_drivers
from naryad.solve.vehicles import solve_vehicles

EXAMPLES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "samples" / "api_examples.json"
TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
MAX_WARNINGS = 50
OK, BAD, NOT_FOUND, TOO_EARLY = (HTTPStatus.OK, HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND,
                                 HTTPStatus.CONFLICT)
EVENT_TYPES = ("breakdown", "accident", "no_show")


class ApiError(Exception):
    """Ответ с ошибкой: код и {"error": текст, ...}."""

    def __init__(self, status: int, text: str, **extra):
        super().__init__(text)
        self.status, self.payload = int(status), {"error": text, **extra}


def hm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def parse_time(text, what: str = "время") -> int:
    found = TIME.match(str(text or ""))
    if not found or int(found.group(2)) >= 60:
        raise ApiError(BAD, f"{what}: в виде ЧЧ:ММ, после полуночи часы идут дальше 24")
    return int(found.group(1)) * 60 + int(found.group(2))


@dataclass
class DayRecord:
    id: str
    raw: dict
    day: Day
    plan: Plan | None = None
    state: OpsState | None = None
    history_before: History = field(default_factory=History)   # прошлые дни водителей
    history_after: History = field(default_factory=History)    # то же плюс этот день
    log: list = field(default_factory=list)
    seconds: float = 0.0


ROUTES = [
    ("GET", r"/api/labor", "labor"),
    ("GET", r"/api/days", "list_days"),
    ("POST", r"/api/days", "add_day"),
    ("GET", r"/api/days/([^/]+)", "get_day"),
    ("POST", r"/api/days/([^/]+)/plan", "make_plan"),
    ("GET", r"/api/days/([^/]+)/state", "get_state"),
    ("GET", r"/api/days/([^/]+)/explain/vehicle/([^/]+)", "explain_vehicle"),
    ("GET", r"/api/days/([^/]+)/explain/driver/([^/]+)", "explain_driver"),
    ("GET", r"/api/days/([^/]+)/explain/unfilled/([^/]+)", "explain_unfilled"),
    ("GET", r"/api/days/([^/]+)/explain/summary", "explain_summary"),
    ("GET", r"/api/days/([^/]+)/explain/interval/([^/]+)", "explain_interval"),
    ("POST", r"/api/days/([^/]+)/events/options", "event_options"),
    ("POST", r"/api/days/([^/]+)/events/apply", "event_apply"),
    ("GET", r"/api/days/([^/]+)/log", "get_log"),
]


class Engine:
    """Дни в памяти и ответы на запросы."""

    def __init__(self):
        self.days: dict[str, DayRecord] = {}
        self._next = 1
        self._lock = threading.Lock()

    def handle(self, method: str, path: str, query: dict | None = None,
               body: dict | None = None) -> tuple[int, dict]:
        """(код ответа, JSON). Ошибки движка наружу не выходят, только ApiError."""
        path = path.rstrip("/") or "/"
        allowed = set()
        with self._lock:
            try:
                for verb, pattern, name in ROUTES:
                    found = re.fullmatch(pattern, path)
                    if not found:
                        continue
                    allowed.add(verb)
                    if verb == method:
                        handler = getattr(self, name)
                        return OK, handler(*found.groups(), query=query or {},
                                           body=body if body is not None else {})
                if allowed:
                    raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED,
                                   f"метод {method} не поддерживается, можно: {', '.join(sorted(allowed))}")
                raise ApiError(NOT_FOUND, "нет такого адреса")
            except ApiError as error:
                return error.status, error.payload

    # --- справочное --------------------------------------------------------------

    def labor(self, query, body):
        raw = json.loads(LABOR_FILE.read_text(encoding="utf-8"))
        labor = load_labor()
        return {"preset": labor_preset_name(), "presets": list(labor_presets()),
                "norms": [{"name": k, "value": labor[k], "rule": v["rule"], "p160": v["p160"],
                           "status": v["status"]}
                          for k, v in raw.items() if not k.startswith("_")]}

    def list_days(self, query, body):
        return {"days": [{"day_id": r.id, "date": r.day.meta["date"], "day_type": r.day.meta["day_type"],
                          "planned": r.plan is not None, "events": len(r.log)}
                         for r in self.days.values()]}

    # --- день ---------------------------------------------------------------------

    def add_day(self, query, body):
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON")
        if "parks" in body or "meta" in body:
            raw = body
        else:
            raw = generate(**self._inputs(body))
        checked = check(raw)
        if checked["errors"]:
            raise ApiError(BAD, "данные с ошибками, день не принят", errors=checked["errors"][:MAX_WARNINGS])
        record = DayRecord(id=f"day-{self._next}", raw=raw, day=Day.from_dict(raw))
        self._next += 1
        self.days[record.id] = record
        meta = raw["meta"]
        return {"day_id": record.id, "date": meta["date"], "day_type": meta["day_type"],
                "moment": meta.get("moment"), "preset": meta.get("preset"),
                "counts": {k: len(raw[k]) for k in ("parks", "routes", "duties", "shifts", "vehicles", "drivers")},
                "warnings": checked["warnings"][:MAX_WARNINGS]}

    @staticmethod
    def _inputs(body: dict) -> dict:
        preset = body.get("preset") or "case"
        if preset not in ("case", "park7"):
            raise ApiError(BAD, "preset: case или park7")
        day = body.get("date") or "2026-10-05"
        try:
            Date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ApiError(BAD, "date: в виде ГГГГ-ММ-ДД") from None
        try:
            seed = int(body.get("seed", 1))
        except (TypeError, ValueError):
            raise ApiError(BAD, "seed: целое число") from None
        moment = body.get("moment") or "morning"
        if moment not in ("plan", "morning"):
            raise ApiError(BAD, "moment: plan или morning")
        return {"preset": preset, "day": day, "seed": seed, "moment": moment}

    def _day(self, day_id: str) -> DayRecord:
        if day_id not in self.days:
            raise ApiError(NOT_FOUND, f"Нет дня {day_id}")
        return self.days[day_id]

    def _planned(self, day_id: str) -> DayRecord:
        record = self._day(day_id)
        if record.plan is None:
            raise ApiError(TOO_EARLY, f"Сначала постройте план: POST /api/days/{day_id}/plan")
        return record

    def get_day(self, day_id, query, body):
        return self._day(day_id).raw

    def make_plan(self, day_id, query, body):
        record = self._day(day_id)
        history = History()
        source = body.get("history_from")
        if source:
            previous = self._planned(source)
            if previous.day.meta["date"] >= record.day.meta["date"]:
                raise ApiError(BAD, f"history_from: день {source} должен быть раньше {record.day.meta['date']}")
            history = copy.deepcopy(previous.history_after)
        record.history_before = copy.deepcopy(history)
        started = time.perf_counter()
        plan = solve_drivers(record.day, solve_vehicles(record.day), history)
        record.seconds = round(time.perf_counter() - started, 2)
        record.plan, record.history_after, record.log = plan, history, []
        record.state = OpsState.from_plan(record.day, plan, record.history_before)
        return {"plan": plan.to_dict(), "violations": _violations(check_plan(record.day, plan)),
                "summary": explain.day_summary(record.day, plan, record.history_before),
                "seconds": record.seconds}

    def get_state(self, day_id, query, body):
        return _state_dict(self._planned(day_id))

    def get_log(self, day_id, query, body):
        return {"log": self._day(day_id).log}

    # --- объяснения (по утреннему плану) -----------------------------------------

    def explain_vehicle(self, day_id, duty_id, query, body):
        record = self._planned(day_id)
        if duty_id not in record.day.duties:
            raise ApiError(NOT_FOUND, f"Нет наряда {duty_id}")
        if duty_id not in record.plan.vehicles:
            return explain.why_unfilled(record.day, record.plan, duty_id, record.history_before)
        return explain.why_vehicle(record.day, record.plan, duty_id)

    def explain_driver(self, day_id, shift_id, query, body):
        record = self._planned(day_id)
        if shift_id not in record.day.shifts:
            raise ApiError(NOT_FOUND, f"Нет смены {shift_id}")
        return explain.why_driver(record.day, record.plan, shift_id, record.history_before)

    def explain_unfilled(self, day_id, item_id, query, body):
        record = self._planned(day_id)
        if item_id in record.day.duties:
            what, where = "наряд закрыт", "explain/vehicle"
        elif item_id in record.day.shifts:
            what, where = "смена закрыта", "explain/driver"
        else:
            raise ApiError(NOT_FOUND, f"Нет наряда или смены {item_id}")
        if item_id not in record.plan.unfilled:
            raise ApiError(NOT_FOUND, f"{what}, смотрите {where}")
        return explain.why_unfilled(record.day, record.plan, item_id, record.history_before)

    def explain_summary(self, day_id, query, body):
        record = self._planned(day_id)
        return explain.day_summary(record.day, record.plan, record.history_before)

    def explain_interval(self, day_id, route_id, query, body):
        record = self._planned(day_id)
        if route_id not in record.day.routes:
            raise ApiError(NOT_FOUND, f"Нет маршрута {route_id}")
        if "t" not in query:
            raise ApiError(BAD, "нужен параметр t: момент в виде ЧЧ:ММ")
        return explain.interval(record.day, record.plan, route_id, parse_time(query["t"], "t"))

    # --- события ------------------------------------------------------------------

    def _event(self, record: DayRecord, body: dict):
        if not isinstance(body, dict):
            raise ApiError(BAD, "событие: объект JSON")
        kind = body.get("type")
        if kind not in EVENT_TYPES:
            raise ApiError(BAD, f"type: {', '.join(EVENT_TYPES)}")
        at = parse_time(body.get("at"), "at")
        if record.log and at < parse_time(record.log[-1]["at"]):
            raise ApiError(BAD, f"событие в {hm(at)} раньше последнего в журнале ({record.log[-1]['at']}): "
                                "события применяются по порядку времени")
        state = record.state
        if kind == "no_show":
            driver_id = body.get("driver_id")
            if driver_id not in record.day.drivers:
                raise ApiError(NOT_FOUND, f"Нет водителя {driver_id}")
            if not any(s.who == driver_id and s.end > at for segs in state.drivers.values() for s in segs):
                raise ApiError(BAD, f"Водитель {driver_id} после {hm(at)} не на смене")
            return NoShow(driver_id, at)
        vehicle_id = body.get("vehicle_id")
        if vehicle_id not in record.day.vehicles:
            raise ApiError(NOT_FOUND, f"Нет автобуса {vehicle_id}")
        if state.duty_of_vehicle(vehicle_id, at) is None:
            raise ApiError(BAD, f"Автобус {vehicle_id} в {hm(at)} не на линии")
        if kind == "accident":
            return Accident(vehicle_id, at)
        duration = body.get("duration_min")
        if duration is not None:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                raise ApiError(BAD, "duration_min: целое число минут или без него") from None
            if duration <= 0:
                raise ApiError(BAD, "duration_min: больше нуля")
        return Breakdown(vehicle_id, at, duration)

    def event_options(self, day_id, query, body):
        record = self._planned(day_id)
        event = self._event(record, body)
        options = options_for(record.state, event)
        return {"event": _event_dict(event),
                "options": [_option_dict(record.state, event, options, i) for i in range(len(options))]}

    def event_apply(self, day_id, query, body):
        record = self._planned(day_id)
        event = self._event(record, body.get("event") or {})
        options = options_for(record.state, event)
        try:
            index = int(body.get("option"))
        except (TypeError, ValueError):
            raise ApiError(BAD, "option: номер варианта из ответа events/options") from None
        if not 0 <= index < len(options):
            raise ApiError(BAD, f"option: от 0 до {len(options) - 1}")
        record.state = apply(record.state, event, options[index])
        record.log.append({**_event_dict(event), "option": index, "title": options[index].title})
        out = _state_dict(record)
        out["violations"] = _violations(check_state(record.state))
        return out


# --- сериализация -----------------------------------------------------------------

def _violations(items: list) -> list:
    return [{"code": v.code, "text": v.text, "ids": list(v.ids)} for v in items]


def _event_dict(event) -> dict:
    if isinstance(event, NoShow):
        return {"type": "no_show", "driver_id": event.driver_id, "at": hm(event.at)}
    if isinstance(event, Accident):
        return {"type": "accident", "vehicle_id": event.vehicle_id, "at": hm(event.at)}
    return {"type": "breakdown", "vehicle_id": event.vehicle_id, "at": hm(event.at),
            "duration_min": event.duration}


def _option_dict(state: OpsState, event, options: list, index: int) -> dict:
    option = options[index]
    return {"index": index, "kind": option.kind, "title": option.title, "cost": round(option.cost, 2),
            "lost_minutes": option.lost_minutes, "note": option.note,
            "explanation": explain.explain_option(state, event, options, index)}


def _segments(items: dict, key: str, who: str) -> list:
    return [{key: item_id, who + "s": [{"from": hm(s.start), "to": hm(s.end), who + "_id": s.who}
                                       for s in sorted(segs, key=lambda s: s.start)]}
            for item_id, segs in sorted(items.items()) if segs]


def _state_dict(record: DayRecord) -> dict:
    state, plan = record.state, record.plan
    return {"meta": {"day_id": record.id, "date": record.day.meta["date"], "events": len(record.log),
                     "labor": labor_preset_name()},
            "duties": _segments(state.vehicles, "duty_id", "vehicle"),
            "shifts": _segments(state.drivers, "shift_id", "driver"),
            "unfilled": [{"id": k, "reason": r} for k, r in sorted(plan.unfilled.items())],
            "transfers": [{"vehicle_id": v, "to_park": p} for v, p in sorted(state.transfers.items())],
            "down_vehicles": [{"vehicle_id": v, "since": hm(t)} for v, t in sorted(state.down_vehicles.items())],
            "down_drivers": [{"driver_id": d, "since": hm(t)} for d, t in sorted(state.down_drivers.items())],
            "log": list(record.log)}


# --- образцы ответов ----------------------------------------------------------------

def make_examples() -> dict:
    """Образцы для docs/CONTRACT.md: день парка №7, сход в 08:40 на P07-R01-WD01, вариант 1."""
    engine = Engine()
    out = {"_about": "Образцы ответов API движка по docs/CONTRACT.md (раздел «API движка», версия 0.1), "
                     "сняты через naryad.web.api.make_examples с дня парка №7 (preset park7, seed 1). "
                     "Ключ - метод и адрес, внутри request (если есть) и response. "
                     "day-2 - тот же день, где каждый седьмой автобус в ремонте: там есть незакрытые наряды."}

    def call(method, path, body=None, query=None, note=None, request=None, keep=True):
        status, payload = engine.handle(method, path, query, body)
        if status != OK:
            raise AssertionError(f"{method} {path}: {status} {payload}")
        if keep:
            key = f"{method} {path}" + (f"?{'&'.join(f'{k}={v}' for k, v in query.items())}" if query else "")
            entry = {"_note": note} if note else {}
            if request is not None or body is not None:
                entry["request"] = body if request is None else request
            entry["response"] = payload
            out[key] = entry
        return payload

    call("GET", "/api/labor")
    first = call("POST", "/api/days", {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"})
    day_id = first["day_id"]
    call("POST", f"/api/days/{day_id}/plan", {})
    call("GET", "/api/days")
    duty, shift, route = "P07-R01-WD01", "P07-R01-WD01-S1", "P07-R01"
    call("GET", f"/api/days/{day_id}/explain/vehicle/{duty}")
    call("GET", f"/api/days/{day_id}/explain/driver/{shift}")
    call("GET", f"/api/days/{day_id}/explain/summary")
    call("GET", f"/api/days/{day_id}/explain/interval/{route}", query={"t": "08:30"})

    short = copy.deepcopy(engine.days[day_id].raw)
    for i, vehicle in enumerate(short["vehicles"]):
        if i % 7 == 0 and vehicle["condition"] == "ok":
            vehicle["condition"], vehicle["repair_days_left"] = "repair", 3
    second = call("POST", "/api/days", short, request="<день целиком: parks, routes, duties, ...>",
                  note="день целиком в теле запроса: образец park7_weekday.json, где каждый седьмой автобус в ремонте")
    plan2 = call("POST", f"/api/days/{second['day_id']}/plan", {}, keep=False)
    unfilled = plan2["plan"]["unfilled"][0]["id"]
    call("GET", f"/api/days/{second['day_id']}/explain/unfilled/{unfilled}")

    state = engine.days[day_id].state
    vehicle = state.vehicle_at(duty, 8 * 60 + 40)
    event = {"type": "breakdown", "vehicle_id": vehicle, "at": "08:40"}
    call("POST", f"/api/days/{day_id}/events/options", event)
    call("POST", f"/api/days/{day_id}/events/apply", {"event": event, "option": 1})
    call("GET", f"/api/days/{day_id}/state", note="после события совпадает с ответом events/apply без поля violations")
    call("GET", f"/api/days/{day_id}/log")

    third = engine.handle("POST", "/api/days", None, {"preset": "park7"})[1]["day_id"]
    out["ошибки"] = {
        "400": engine.handle("POST", f"/api/days/{day_id}/events/options", None,
                             {"type": "breakdown", "vehicle_id": vehicle, "at": "09:00"})[1],
        "404": engine.handle("GET", "/api/days/day-9")[1],
        "409": engine.handle("GET", f"/api/days/{third}/explain/summary")[1],
    }
    return out


def write_examples(path: Path = EXAMPLES_FILE) -> Path:
    path.write_text(json.dumps(make_examples(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# --- сервер -------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """Отдельный сервер API с заголовками CORS: страница с другого порта может звать его напрямую."""

    engine: Engine = Engine()

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        self._json(*self.engine.handle("GET", url.path, query))

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(BAD, {"error": "тело запроса: неверный JSON"})
        self._json(*self.engine.handle("POST", url.path, {}, body))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="API движка AUTODISP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--labor", default=None,
                        help="набор норм из naryad/core/labor_presets.json: current, likely, strict")
    parser.add_argument("--write-examples", action="store_true",
                        help="переписать data/samples/api_examples.json и выйти")
    args = parser.parse_args(argv)
    use_labor_preset(args.labor)
    if args.write_examples:
        print(f"Образцы записаны: {write_examples()}")
        return 0
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"API движка: http://{args.host}:{args.port}/api/labor  (нормы: {labor_preset_name()}, "
          f"остановить - Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
