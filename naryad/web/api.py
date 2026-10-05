"""
API движка (Б5): день в памяти под номером, план, объяснения, события,
ручная правка плана диспетчером, загрузка реестров перевозчика.

Форматы - docs/CONTRACT.md, разделы «Состояние дня» и «API движка».
План на день с несколькими парками строится с перебросками лишних
автобусов между парками (А7), на один парк - без них: перебрасывать некуда.
Образцы ответов - data/samples/api_examples.json: их пишет этот же модуль
(--write-examples), а тест сверяет файл с живыми ответами.

Запуск отдельным сервером (пока страница генератора не подключила модуль):
    python -m naryad.web.api                   # http://localhost:8001
    python -m naryad.web.api --labor likely    # набор норм на весь сервер
    python -m naryad.web.api --write-examples  # переписать образцы ответов

Диспетчер главнее движка: правка, которая нарушает нормы, по умолчанию не
применяется, но с force: true применяется и попадает в журнал вместе со
списком того, что нарушено. Движок не отказывает молча и не меняет решение
диспетчера молча.

Дни лежат в папке на диске и переживают перезапуск сервера (naryad/web/store.py).
Папка задаётся при запуске (--store, по умолчанию store/days), --store none
выключает запись совсем. Внутри процесса Engine() без папки ничего не пишет.
В папке лежит реестр перевозчика: её нет в репозитории и не должно быть.
Объяснения относятся к утреннему плану, состояние дня после событий - отрезками.
Единственная точка входа - Engine.handle(метод, адрес, параметры, тело):
отдаёт код ответа и JSON, сервер страницы генератора может звать её напрямую.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import date as Date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from naryad import explain
from naryad.core.invariants import (LABOR_FILE, check_plan, check_rest, labor_preset_name,
                                    labor_presets, load_labor, use_labor_preset)
from naryad.core.model import Day, Plan
from naryad.data.check import check
from naryad.data import registry
from naryad.data.generate import generate
from naryad.ops.replan import (EDIT_TYPES, Accident, Breakdown, Edit, NoShow, OpsState, apply,
                               check_state, free_drivers, free_vehicles, history_after,
                               manual_edit, options_for)
from naryad.solve.city import add_transfers
from naryad.solve.drivers import History, solve_drivers
from naryad.solve.vehicles import solve_vehicles
from naryad.web.store import (Store, history_from_dict, history_to_dict, plan_from_dict,
                              plan_to_dict, state_from_dict, state_to_dict)

EXAMPLES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "samples" / "api_examples.json"
TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
MAX_WARNINGS = 50
OK, BAD, NOT_FOUND, TOO_EARLY = (HTTPStatus.OK, HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND,
                                 HTTPStatus.CONFLICT)
BLOCKED = HTTPStatus.CONFLICT  # тот же 409: запрос понятен, но в этом состоянии не выполняется
SERVER_ERROR = HTTPStatus.INTERNAL_SERVER_ERROR
EVENT_TYPES = ("breakdown", "accident", "no_show")
MAX_CANDIDATES = 50
MAX_BODY = 64 * 1024 * 1024   # реестр целого города и то меньше
MAX_FILES = 24                # по файлу на сущность с запасом
DEFAULT_STORE = "store/days"  # дни на диске; в них реестр перевозчика, в репозиторий не кладём


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
    source: str | None = None   # день, от которого взята память водителей


ROUTES = [
    ("GET", r"/api/labor", "labor"),
    ("GET", r"/api/days", "list_days"),
    ("POST", r"/api/days", "add_day"),
    ("POST", r"/api/days/import", "import_registry"),
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
    ("GET", r"/api/days/([^/]+)/edits/options", "edit_options"),
    ("POST", r"/api/days/([^/]+)/edits", "edit_plan"),
    ("GET", r"/api/days/([^/]+)/log", "get_log"),
]


class Engine:
    """Дни в памяти и ответы на запросы."""

    def __init__(self, store=None):
        self.days: dict[str, DayRecord] = {}
        self._next = 1
        self._lock = threading.Lock()
        self.store = Store(store)
        self._restore()

    # --- хранение на диске --------------------------------------------------------

    def _restore(self) -> None:
        """Прочитать дни из папки. Испорченный день пропускается с сообщением."""
        highest = 0
        for day_id, stored, live in self.store.read_all():
            raw = stored.get("raw")
            try:
                day = Day.from_dict(raw)
            except (KeyError, TypeError, ValueError) as error:
                print(f"День {day_id} пропущен: данные больше не читаются "
                      f"({type(error).__name__} {error})", file=sys.stderr)
                continue
            record = DayRecord(id=day_id, raw=raw, day=day)
            if live and live.get("plan") and live.get("state"):
                record.plan = plan_from_dict(live["plan"])
                record.state = state_from_dict(live["state"], day)
                record.history_before = history_from_dict(live.get("history_before"))
                record.history_after = history_from_dict(live.get("history_after"))
                record.log = list(live.get("log") or [])
                record.seconds = live.get("seconds") or 0.0
                record.source = live.get("source")
            self.days[day_id] = record
            highest = max(highest, self._number(day_id))
        self._next = highest + 1

    def _save(self, record: DayRecord) -> None:
        """Записать живую часть дня: план, состояние, память водителей, журнал."""
        if not self.store:
            return
        self.store.save_live(record.id, {
            "plan": plan_to_dict(record.plan) if record.plan is not None else None,
            "state": state_to_dict(record.state) if record.state is not None else None,
            "history_before": history_to_dict(record.history_before),
            "history_after": history_to_dict(record.history_after),
            "log": record.log, "seconds": record.seconds, "source": record.source})

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
            except Exception as error:  # ошибка движка, а не запроса
                traceback.print_exc()
                print(f"ОШИБКА ДВИЖКА: {method} {path}: {type(error).__name__}", file=sys.stderr)
                return SERVER_ERROR, {"error": "Внутренняя ошибка сервиса, запрос не выполнен. "
                                               "Подробности - в журнале сервера.",
                                      "kind": type(error).__name__}

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
                          "planned": r.plan is not None, "events": _count(r.log, "event"),
                          "edits": _count(r.log, "edit")}
                         for r in self.days.values()]}

    # --- день ---------------------------------------------------------------------

    def add_day(self, query, body):
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON")
        if "parks" in body or "meta" in body:
            raw = body
        else:
            raw = generate(**self._inputs(body))
        return self._register(raw)

    def _register(self, raw: dict) -> dict:
        """Проверить набор данных и положить день в память под новым номером."""
        checked = check(raw)
        if checked["errors"]:
            raise ApiError(BAD, "данные с ошибками, день не принят", errors=checked["errors"][:MAX_WARNINGS])
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else None
        if not meta or not meta.get("date") or not meta.get("day_type"):
            raise ApiError(BAD, "meta: нужны дата дня (ГГГГ-ММ-ДД) и тип дня (weekday или weekend)")
        try:
            day = Day.from_dict(raw)
        except (KeyError, TypeError, ValueError) as error:
            raise ApiError(BAD, f"данные дня не читаются: {type(error).__name__} {error}") from None
        day_id = f"day-{self._next}"
        payload = {"day_id": day_id, "date": meta["date"], "day_type": meta["day_type"],
                   "moment": meta.get("moment"), "preset": meta.get("preset"),
                   "counts": {k: len(raw[k]) for k in ("parks", "routes", "duties", "shifts", "vehicles", "drivers")},
                   "warnings": checked["warnings"][:MAX_WARNINGS]}
        record = DayRecord(id=day_id, raw=raw, day=day)
        self.store.save_day(day_id, raw)
        self.days[day_id] = record  # последним: ответ уже собран
        self._next += 1
        self._save(record)
        return payload

    def import_registry(self, query, body):
        """Загрузить день из реестра перевозчика: CSV в теле запроса или книга Excel.

        Реестр не обязан совпадать с нашим форматом: столбцы узнаются по
        синонимам, слова переводятся в коды, то, что движку не нужно,
        заполняется само. Всё это попадает в report - отчёт о загрузке.
        Он возвращается и при удаче, и при отказе: по нему видно, какой
        столбец чем понят и что спросить у перевозчика.

        dry_run: true читает реестр и отдаёт отчёт, но день не создаёт.
        """
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON с полями files или workbook")
        dry_run = body.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ApiError(BAD, "dry_run: true или false")
        files = self._files(body.get("files"))
        workbook = self._workbook(body.get("workbook"))
        if not files and workbook is None:
            raise ApiError(BAD, "нужен хотя бы один файл: files с текстом CSV "
                                "или workbook с книгой Excel в base64")
        meta = body.get("meta") or {}
        if not isinstance(meta, dict):
            raise ApiError(BAD, "meta: объект с датой дня и типом дня")
        if not meta.get("date") or not meta.get("day_type"):
            raise ApiError(BAD, "meta: нужны дата дня (ГГГГ-ММ-ДД) и тип дня (weekday или weekend). "
                                "В реестре их нет, поэтому их задаёт тот, кто его загружает")
        try:
            raw, report = registry.load(files=files, workbook=workbook, meta=meta)
        except ValueError as error:
            raise ApiError(BAD, f"реестр не прочитан: {error}") from None
        if report["errors"]:
            raise ApiError(BAD, "реестр прочитан с ошибками, день не принят", report=report)
        counts = {line["entity"]: line["rows"] for line in report["entities"]}
        if dry_run:
            return {"day_id": None, "date": meta["date"], "day_type": meta["day_type"],
                    "counts": counts, "warnings": [], "report": report}
        payload = self._register(raw)
        payload["report"] = report
        payload["warnings"] = (report["warnings"] + payload["warnings"])[:MAX_WARNINGS]
        return payload

    @staticmethod
    def _files(raw) -> dict:
        if raw is None:
            return {}
        if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
            raise ApiError(BAD, "files: объект {название файла или листа: текст CSV}")
        if len(raw) > MAX_FILES:
            raise ApiError(BAD, f"files: не больше {MAX_FILES} файлов за раз")
        return {str(k): v for k, v in raw.items()}

    @staticmethod
    def _workbook(raw):
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ApiError(BAD, "workbook: книга Excel (.xlsx) в base64 строкой")
        try:
            return base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            raise ApiError(BAD, "workbook: строка не читается как base64") from None

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
        """Утренний план. Запись дня меняется только после удачного расчёта.

        Сначала проверяется всё тело запроса: отклонённый запрос не должен
        портить план и память водителей, которые уже показаны диспетчеру.
        """
        record = self._day(day_id)
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON")
        history, labor = History(), load_labor()
        source = body.get("history_from")
        notes = []
        if source is not None:
            if not isinstance(source, str):
                raise ApiError(BAD, "history_from: номер дня строкой, например day-1")
            previous = self._planned(source)
            if previous.day.meta["date"] >= record.day.meta["date"]:
                raise ApiError(BAD, f"history_from: день {source} должен быть раньше {record.day.meta['date']}")
            history = copy.deepcopy(previous.history_after)
            if previous.log:
                notes.append(f"память водителей взята по факту дня {source}: "
                             f"в нём применено событий {len(previous.log)}")
        earlier = self._earlier(record)
        if source is None and earlier:
            notes.append("history_from не передан: водители считаются полностью отдохнувшими. "
                         f"Есть более ранние дни этого же набора данных с планом: "
                         f"{', '.join(r.id for r in earlier)}")
        transfers, gas_parks = self._transfer_options(record, body)

        started = time.perf_counter()
        before = copy.deepcopy(history)
        vehicles = solve_vehicles(record.day)
        if transfers:
            add_transfers(record.day, vehicles, gas_parks=gas_parks)
        plan = solve_drivers(record.day, vehicles, history)
        seconds = round(time.perf_counter() - started, 2)
        series = [(r.day, r.plan) for r in earlier] + [(record.day, plan)]
        answer = {"plan": plan.to_dict(), "violations": _violations(check_plan(record.day, plan)),
                  "rest_violations": _violations(check_rest(series, labor)),
                  "summary": explain.day_summary(record.day, plan, before),
                  "notes": notes, "seconds": seconds}

        record.history_before, record.source = before, source
        record.plan, record.log, record.seconds = plan, [], seconds
        record.state = OpsState.from_plan(record.day, plan, before)
        record.history_after = history_after(record.state, labor)
        self._save(record)
        return answer

    def _earlier(self, record: DayRecord) -> list:
        """Дни с планами до этого дня, по одному на дату: для проверки отдыха.

        Берутся все дни в памяти, а не только цепочка history_from: если
        диспетчер построил два дня независимо, нарушения отдыха между ними
        всё равно надо показать. Считаются только дни одного и того же
        набора данных (те же парки, та же настройка, тот же номер набора):
        у другого города номера водителей могут совпадать случайно, и
        сверять по ним отдых нельзя. Если на одну дату загружено несколько
        вариантов дня, берётся последний загруженный.
        """
        key, today = self._city_key(record.day), record.day.meta["date"]
        by_date: dict = {}
        for other in self.days.values():
            if other.id == record.id or other.plan is None:
                continue
            date_of = other.day.meta["date"]
            if date_of >= today or self._city_key(other.day) != key:
                continue
            kept = by_date.get(date_of)
            if kept is None or self._number(other.id) > self._number(kept.id):
                by_date[date_of] = other
        return [r for _, r in sorted(by_date.items())]

    @staticmethod
    def _city_key(day) -> tuple:
        """Признак «это тот же набор данных»: парки, настройка, вводные, номер набора."""
        meta = day.meta
        return (frozenset(day.parks), meta.get("preset"), meta.get("seed"),
                json.dumps(meta.get("inputs") or {}, sort_keys=True, ensure_ascii=False))

    @staticmethod
    def _number(day_id: str) -> int:
        tail = day_id.rsplit("-", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    @staticmethod
    def _transfer_options(record: DayRecord, body: dict) -> tuple[bool, set | None]:
        """Перебрасывать ли автобусы между парками и куда можно газовые.

        По умолчанию переброски включены, когда в дне больше одного парка:
        одному парку перебрасывать некуда. gas_parks - парки с газовой
        инфраструктурой, куда можно отдать газовый автобус; без него
        ограничения нет (в данных генератора признака газа у парка нет).
        """
        transfers = body.get("transfers")
        if transfers is None:
            transfers = len(record.day.parks) > 1
        if not isinstance(transfers, bool):
            raise ApiError(BAD, "transfers: true или false")
        raw = body.get("gas_parks")
        if raw is None:
            return transfers, None
        if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
            raise ApiError(BAD, "gas_parks: список номеров парков, например [\"P03\", \"P07\"]")
        unknown = sorted(set(raw) - set(record.day.parks))
        if unknown:
            raise ApiError(NOT_FOUND, f"Нет парков: {', '.join(unknown)}")
        return transfers, set(raw)

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
        at = parse_time(query["t"], "t")
        numbers = explain.interval(record.day, record.plan, route_id, at)
        route = record.day.routes[route_id]
        planned, actual = numbers["planned_min"], numbers["actual_min"]
        grew = actual - planned
        return {"question": f"Какой интервал на маршруте {route.number} в {hm(at)}?",
                "answer": f"По плану {planned} мин, сейчас {actual} мин"
                          + (" - как по плану" if grew <= 0 else f", на {grew} мин больше"),
                "reasons": [f"По плану на маршруте {numbers['planned_buses']} автобусов, "
                            f"работает {numbers['running_buses']}"],
                "numbers": numbers}

    # --- события ------------------------------------------------------------------

    def _event(self, record: DayRecord, body: dict):
        if not isinstance(body, dict):
            raise ApiError(BAD, "событие: объект JSON")
        kind = body.get("type")
        if kind not in EVENT_TYPES:
            raise ApiError(BAD, f"type: {', '.join(EVENT_TYPES)}")
        at = parse_time(body.get("at"), "at")
        last = next((e for e in reversed(record.log) if e.get("kind", "event") == "event"), None)
        if last and at < parse_time(last["at"]):
            raise ApiError(BAD, f"событие в {hm(at)} раньше последнего в журнале ({last['at']}): "
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
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON с полями события")
        event = self._event(record, body)
        options = options_for(record.state, event)
        return {"event": _event_dict(event),
                "options": [_option_dict(record.state, event, options, i) for i in range(len(options))]}

    def event_apply(self, day_id, query, body):
        record = self._planned(day_id)
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON с полями event и option")
        event = self._event(record, body.get("event") or {})
        options = options_for(record.state, event)
        try:
            index = int(body.get("option"))
        except (TypeError, ValueError):
            raise ApiError(BAD, "option: номер варианта из ответа events/options") from None
        if not 0 <= index < len(options):
            raise ApiError(BAD, f"option: от 0 до {len(options) - 1}")
        after = apply(record.state, event, options[index])
        entry = {"kind": "event", **_event_dict(event), "option": index,
                 "title": options[index].title}
        violations = _violations(check_state(after))
        record.state, record.log = after, record.log + [entry]
        record.history_after = history_after(after)  # память по факту, а не по утреннему плану
        self._save(record)
        out = _state_dict(record)
        out["violations"] = violations
        return out


    # --- ручная правка плана диспетчером ------------------------------------------

    def _edit(self, record: DayRecord, body: dict) -> Edit:
        """Разобрать и проверить правку. Законность норм здесь не проверяется."""
        kind = body.get("type")
        if kind not in EDIT_TYPES:
            raise ApiError(BAD, f"type: {', '.join(EDIT_TYPES)}")
        key, item, what = self._edit_target(record, kind, body)
        start, end = self._window(item, what, body)
        day, who = record.day, None
        if kind == "set_vehicle":
            who = body.get("vehicle_id")
            if who not in day.vehicles:
                raise ApiError(NOT_FOUND, f"Нет автобуса {who}")
        elif kind == "set_driver":
            who = body.get("driver_id")
            if who not in day.drivers:
                raise ApiError(NOT_FOUND, f"Нет водителя {who}")
        whole = (start, end) == (item.start, item.end)
        when = "" if whole else f" с {hm(start)} до {hm(end)}"
        if kind == "set_vehicle":
            title = f"Диспетчер поставил автобус {day.vehicles[who].board_number} на наряд {key}{when}"
        elif kind == "clear_vehicle":
            title = f"Диспетчер снял автобус с наряда {key}{when}"
        elif kind == "set_driver":
            title = f"Диспетчер поставил водителя {day.drivers[who].tab_number} на смену {key}{when}"
        else:
            title = f"Диспетчер снял водителя со смены {key}{when}"
        return Edit(kind, key, who, start, end, title)

    @staticmethod
    def _edit_target(record: DayRecord, kind: str, body: dict) -> tuple:
        if kind.endswith("vehicle"):
            key, where, what = body.get("duty_id"), record.day.duties, "наряд"
        else:
            key, where, what = body.get("shift_id"), record.day.shifts, "смена"
        field_name = "duty_id" if what == "наряд" else "shift_id"
        if not isinstance(key, str) or not key:
            raise ApiError(BAD, f"нужно поле {field_name}: номер {what}а"
                                if what == "наряд" else f"нужно поле {field_name}: номер смены")
        if key not in where:
            raise ApiError(NOT_FOUND, f"Нет наряда {key}" if what == "наряд" else f"Нет смены {key}")
        return key, where[key], what

    @staticmethod
    def _window(item, what: str, body: dict) -> tuple[int, int]:
        """Окно правки. По умолчанию - всё время наряда или смены."""
        start = parse_time(body["from"], "from") if body.get("from") is not None else item.start
        end = parse_time(body["to"], "to") if body.get("to") is not None else item.end
        if not item.start <= start < end <= item.end:
            raise ApiError(BAD, f"окно {hm(start)}-{hm(end)} пустое или выходит за время "
                                f"{'наряда' if what == 'наряд' else 'смены'} "
                                f"({hm(item.start)}-{hm(item.end)})")
        return start, end

    def edit_plan(self, day_id, query, body):
        """Поставить или снять автобус (водителя) вручную.

        Правка, которая создаёт новые нарушения норм, по умолчанию не
        применяется: ответ 409 со списком нарушений. С force: true она
        применяется и записывается в журнал как решение диспетчера вместе
        с тем, что именно нарушено. Нарушения, которые были в состоянии и
        до правки, диспетчеру не приписываются.

        Состояние дня меняется только после успешной проверки: отклонённая
        правка не портит того, что уже показано.
        """
        record = self._planned(day_id)
        if not isinstance(body, dict):
            raise ApiError(BAD, "тело запроса: объект JSON с полями правки")
        force = body.get("force", False)
        if not isinstance(force, bool):
            raise ApiError(BAD, "force: true или false")
        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ApiError(BAD, "reason: текст причины или без него")
        edit = self._edit(record, body)

        was = {_mark(v) for v in check_state(record.state)}
        after = manual_edit(record.state, edit)
        violations = check_state(after)
        added = [v for v in violations if _mark(v) not in was]
        if added and not force:
            raise ApiError(BLOCKED, "Правка нарушает нормы и не применена",
                           violations=_violations(added),
                           hint="повторите запрос с force: true - правка будет применена "
                                "и записана в журнал как решение диспетчера")

        entry = {"kind": "edit", **_edit_dict(edit), "title": edit.title,
                 "forced": bool(added), "violations": _violations(added)}
        if reason:
            entry["reason"] = reason
        record.state, record.log = after, record.log + [entry]
        record.history_after = history_after(after)  # память по факту, а не по утреннему плану
        self._save(record)
        out = _state_dict(record)
        out["violations"] = _violations(violations)
        out["new_violations"] = _violations(added)
        return out

    def edit_options(self, day_id, query, body):
        """Кого законно можно поставить на наряд (смену) в окне времени.

        Интерфейс берёт отсюда список для выбора, чтобы диспетчер не искал
        подходящий автобус или водителя среди всех и не упирался в отказ.
        """
        record = self._planned(day_id)
        day, state = record.day, record.state
        duty_id, shift_id = query.get("duty_id"), query.get("shift_id")
        if (duty_id is None) == (shift_id is None):
            raise ApiError(BAD, "нужен ровно один параметр: duty_id или shift_id")
        kind = "set_vehicle" if duty_id else "set_driver"
        key, item, what = self._edit_target(record, kind, {"duty_id": duty_id, "shift_id": shift_id})
        start, end = self._window(item, what, query)
        head = {("duty_id" if duty_id else "shift_id"): key, "from": hm(start), "to": hm(end)}
        if duty_id:
            found = free_vehicles(state, day.duties[key], start, end)
            shown = [{"vehicle_id": v, "board_number": day.vehicles[v].board_number,
                      "class": day.vehicles[v].cls} for v in found[:MAX_CANDIDATES]]
            return {**head, "vehicles": shown, "total": len(found), "shown": len(shown)}
        duty = day.duties[day.shifts[key].duty_id]
        bus = state.vehicle_at(duty.id, start)
        cls = day.vehicles[bus].cls if bus else None
        found = free_drivers(state, duty, start, end, load_labor(), cls)
        shown = [{"driver_id": d, "tab_number": day.drivers[d].tab_number,
                  "own_vehicle": day.drivers[d].home_vehicle_id == bus} for d in found[:MAX_CANDIDATES]]
        return {**head, "for_vehicle": bus, "drivers": shown,
                "total": len(found), "shown": len(shown)}


# --- сериализация -----------------------------------------------------------------

def _count(log: list, kind: str) -> int:
    """Сколько в журнале записей этого вида. Старые записи без вида - события."""
    return sum(1 for entry in log if entry.get("kind", "event") == kind)


def _mark(violation) -> tuple:
    return (violation.code, violation.text, tuple(violation.ids))


def _edit_dict(edit: Edit) -> dict:
    out = {"type": edit.type, "from": hm(edit.start), "to": hm(edit.end)}
    if edit.type.endswith("vehicle"):
        out["duty_id"] = edit.key
        if edit.who:
            out["vehicle_id"] = edit.who
    else:
        out["shift_id"] = edit.key
        if edit.who:
            out["driver_id"] = edit.who
    return out


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
    """Состояние дня для интерфейса.

    unfilled - незакрытое на этот момент, а не утром: наряд, закрытый
    диспетчером вручную, из списка уходит, а наряд, с которого он снял
    автобус, в список попадает с причиной removed_by_dispatcher. Иначе
    один и тот же наряд был бы и с автобусом, и в незакрытых, а снятый
    автобус пропадал бы из виду совсем.

    Смены резервных нарядов, которым движок не ставил водителя, в список
    не попадают: они не незакрыты, они не нужны.
    """
    state, plan = record.state, record.plan
    filled = {k for k, segs in {**state.vehicles, **state.drivers}.items() if segs}
    reasons = dict(plan.unfilled)
    for entry in record.log:
        if entry.get("kind") == "edit" and str(entry.get("type", "")).startswith("clear"):
            reasons.setdefault(entry.get("duty_id") or entry.get("shift_id"), "removed_by_dispatcher")
    return {"meta": {"day_id": record.id, "date": record.day.meta["date"],
                     "events": _count(record.log, "event"), "edits": _count(record.log, "edit"),
                     "labor": labor_preset_name()},
            "duties": _segments(state.vehicles, "duty_id", "vehicle"),
            "shifts": _segments(state.drivers, "shift_id", "driver"),
            "unfilled": [{"id": k, "reason": r} for k, r in sorted(reasons.items())
                         if k and k not in filled],
            "transfers": [{"vehicle_id": v, "to_park": p} for v, p in sorted(state.transfers.items())],
            "down_vehicles": [{"vehicle_id": v, "since": hm(t)} for v, t in sorted(state.down_vehicles.items())],
            "down_drivers": [{"driver_id": d, "since": hm(t)} for d, t in sorted(state.down_drivers.items())],
            "log": list(record.log)}


# --- образцы ответов ----------------------------------------------------------------

def make_examples() -> dict:
    """Образцы для docs/CONTRACT.md: день парка №7, сход в 08:40 на P07-R01-WD01, вариант 1."""
    engine = Engine()
    spare = "P07-R02-WD01"   # наряд, на котором показана ручная правка
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

    # ручная правка плана: сначала кого можно поставить, потом снятие и постановка
    free = call("GET", f"/api/days/{day_id}/edits/options", query={"duty_id": spare},
                note="кого законно можно поставить на наряд; окно по умолчанию - весь наряд")
    call("POST", f"/api/days/{day_id}/edits",
         {"type": "clear_vehicle", "duty_id": spare, "reason": "автобус нужен на важном маршруте"},
         keep=False)
    call("POST", f"/api/days/{day_id}/edits",
         {"type": "set_vehicle", "duty_id": spare, "vehicle_id": free["vehicles"][0]["vehicle_id"]},
         note=f"до этого с наряда {spare} сняли автобус тем же адресом с type: clear_vehicle - "
              "тогда ответ такой же, но наряд стоит в unfilled с причиной removed_by_dispatcher. "
              "Здесь на него поставлен свободный автобус: new_violations пуст, правка в журнале")

    # загрузка реестра перевозчика: нарочно маленький, с русскими заголовками
    tiny = {
        "Парки": "Код парка;Наименование;Состояние;Выпуск будни;Выпуск выходные\n"
                 "П7;Автобусный парк №7;работает;1;1\n",
        "Маршруты": "Код;Парк;Номер маршрута;Допустимые классы;Важность;Время оборота\n"
                    "М3;П7;3;большой;высокая;160\n",
        "Наряды": "Наряд;Парк;Тип;Маршрут;Класс автобуса;Тип дня;Выход;Заезд;Смен\n"
                  "Н1;П7;линейный;М3;большой;будни;4:50;13:20;1\n",
        "Смены": "Код смены;Номер наряда;Смена;Начало;Окончание\nС1;Н1;1;4:50;13:20\n",
        "Автобусы": "Гаражный номер;Класс;Топливо;Парк;Тех. состояние\n"
                    "7001;большой;газ;П7;исправен\n",
        "Водители": "Табельный номер;ФИО;Парк;Допуск;График;Медосмотр\n"
                    "070001;Макаров Д. В.;П7;большой;работает;прошел\n",
    }
    registry_meta = {"date": "2026-10-05", "day_type": "weekday", "moment": "morning"}
    call("POST", "/api/days/import", {"files": tiny, "meta": registry_meta, "dry_run": True},
         note="dry_run: реестр прочитан, отчёт отдан, день не создан. Без dry_run ответ тот же "
              "плюс day_id и counts по созданному дню. Книга Excel передаётся полем workbook "
              "в base64 вместо files")

    third = engine.handle("POST", "/api/days", None, {"preset": "park7"})[1]["day_id"]
    busy = engine.handle("GET", f"/api/days/{day_id}/state")[1]["duties"][0]
    out["ошибки"] = {
        "400": engine.handle("POST", f"/api/days/{day_id}/events/options", None,
                             {"type": "breakdown", "vehicle_id": vehicle, "at": "09:00"})[1],
        "404": engine.handle("GET", "/api/days/day-9")[1],
        "409": engine.handle("GET", f"/api/days/{third}/explain/summary")[1],
        "400 реестр с непонятым значением": engine.handle(
            "POST", "/api/days/import", None,
            {"files": dict(tiny, **{"Автобусы": tiny["Автобусы"].replace("исправен", "на ходу?")}),
             "meta": registry_meta})[1],
        "409 правка нарушает нормы": engine.handle(
            "POST", f"/api/days/{day_id}/edits", None,
            {"type": "set_vehicle", "duty_id": spare,
             "vehicle_id": busy["vehicles"][0]["vehicle_id"]})[1],
    }
    return out


def write_examples(path: Path = EXAMPLES_FILE) -> Path:
    path.write_text(json.dumps(make_examples(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# --- сервер -------------------------------------------------------------------------

ALLOWED_ORIGINS = ("http://localhost", "http://127.0.0.1", "http://[::1]")


def allowed_origin(origin: str | None) -> str | None:
    """Можно ли отвечать этой странице. Разрешаем только свои, с этого же компьютера.

    День целиком - это реестр перевозчика: номера автобусов, табельные номера
    водителей, наряды. Заголовок «любой сайт» означал, что любая открытая в
    браузере страница может его выкачать, пока у диспетчера запущен сервис.
    """
    if not origin:
        return None
    for prefix in ALLOWED_ORIGINS:
        if origin == prefix or origin.startswith(prefix + ":"):
            return origin
    return None


class Handler(BaseHTTPRequestHandler):
    """Отдельный сервер API. Отвечает только страницам с этого же компьютера."""

    engine: Engine = Engine()

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = allowed_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = allowed_origin(self.headers.get("Origin"))
        self.send_response(HTTPStatus.NO_CONTENT if origin else HTTPStatus.FORBIDDEN)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
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
        except ValueError:
            return self._json(BAD, {"error": "неверный заголовок Content-Length"})
        if length > MAX_BODY:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                              {"error": f"тело запроса больше {MAX_BODY // (1024 * 1024)} МБ"})
        try:
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
    parser.add_argument("--store", default=DEFAULT_STORE,
                        help=f"папка с днями, они переживают перезапуск (по умолчанию {DEFAULT_STORE}); "
                             f"none - не писать на диск")
    parser.add_argument("--write-examples", action="store_true",
                        help="переписать data/samples/api_examples.json и выйти")
    args = parser.parse_args(argv)
    use_labor_preset(args.labor)
    if args.write_examples:
        print(f"Образцы записаны: {write_examples()}")
        return 0
    folder = None if str(args.store).lower() in ("none", "", "нет") else args.store
    Handler.engine = Engine(store=folder)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    where = (f"дни в папке {Path(folder).expanduser().resolve()} (в ней реестр перевозчика, "
             f"наружу не отдавать), загружено: {len(Handler.engine.days)}"
             if folder else "дни только в памяти, перезапуск их сотрёт")
    print(f"API движка: http://{args.host}:{args.port}/api/labor  (нормы: {labor_preset_name()}, "
          f"остановить - Ctrl+C)\n{where}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
