"""
Проверка плана: может ли такой план выйти из движка.

Каждое правило - отдельная функция, которая сама пересчитывает всё по дню
и плану и возвращает список нарушений. Пустой список - план годится.
Через эту проверку проходит любой план: движка, диспетчера, загруженный.

Нарушение - не то же самое, что незакрытый наряд. Незакрытый наряд с
указанной причиной - честный план. Нарушение - план, который нельзя
выпускать: автобус в двух местах, водитель без допуска, неисправный
автобус на линии.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import REASONS, Day, Plan

LABOR_FILE = Path(__file__).with_name("labor.json")


def load_labor(path: Path = LABOR_FILE) -> dict:
    """Нормы труда: имя -> число. Источники и статус лежат в самом файле."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: item["value"] for name, item in raw.items() if not name.startswith("_")}


def shift_limit(labor: dict) -> int:
    """Дневная норма смены: 12 ч при согласовании с профсоюзом, иначе 10 ч (п. 6)."""
    return labor["max_shift_extended_min"] if labor["city_12h_agreed"] else labor["max_shift_min"]


@dataclass(frozen=True)
class Violation:
    code: str
    text: str
    ids: tuple = ()

    def __str__(self) -> str:
        return self.text


def _unknown(day: Day, plan: Plan) -> list:
    out = []
    for duty_id, vehicle_id in plan.vehicles.items():
        if duty_id not in day.duties:
            out.append(Violation("unknown_id", f"Наряда {duty_id} нет в данных дня", (duty_id,)))
        if vehicle_id not in day.vehicles:
            out.append(Violation("unknown_id", f"Автобуса {vehicle_id} нет в данных дня",
                                 (vehicle_id,)))
    for shift_id, driver_id in plan.drivers.items():
        if shift_id not in day.shifts:
            out.append(Violation("unknown_id", f"Смены {shift_id} нет в данных дня", (shift_id,)))
        if driver_id not in day.drivers:
            out.append(Violation("unknown_id", f"Водителя {driver_id} нет в данных дня",
                                 (driver_id,)))
    for item_id in plan.unfilled:
        if item_id not in day.duties and item_id not in day.shifts:
            out.append(Violation("unknown_id", f"Незакрытого {item_id} нет в данных дня",
                                 (item_id,)))
    return out


def _vehicle_twice(day: Day, plan: Plan) -> list:
    duties_of = defaultdict(list)
    for duty_id, vehicle_id in plan.vehicles.items():
        duties_of[vehicle_id].append(duty_id)
    return [Violation("vehicle_twice",
                      f"Автобус {day.vehicles[v].board_number if v in day.vehicles else v} "
                      f"стоит сразу на {len(d)} нарядах: {', '.join(sorted(d))}",
                      (v, *sorted(d)))
            for v, d in duties_of.items() if len(d) > 1]


def _vehicle_fits(day: Day, plan: Plan) -> list:
    out = []
    for duty_id, vehicle_id in plan.vehicles.items():
        duty, vehicle = day.duties.get(duty_id), day.vehicles.get(vehicle_id)
        if duty is None or vehicle is None:
            continue
        name = f"Автобус {vehicle.board_number} на наряде {duty_id}"
        if duty.day_type != day.day_type:
            out.append(Violation("wrong_day_type",
                                 f"Наряд {duty_id} - для другого типа дня ({duty.day_type})",
                                 (duty_id,)))
        if duty.type == "line":
            allowed = day.routes[duty.route_id].allowed_classes
            if vehicle.cls not in allowed:
                out.append(Violation("vehicle_class",
                                     f"{name}: класс {vehicle.cls}, маршрут допускает "
                                     f"{', '.join(allowed)}", (duty_id, vehicle_id)))
        elif vehicle.cls != duty.vehicle_class:
            out.append(Violation("vehicle_class",
                                 f"{name}: класс {vehicle.cls}, резерв нужен класса "
                                 f"{duty.vehicle_class}", (duty_id, vehicle_id)))
        if vehicle.condition != "ok":
            out.append(Violation("vehicle_broken",
                                 f"{name}: автобус неисправен ({vehicle.condition})",
                                 (duty_id, vehicle_id)))
        if vehicle.park_id != duty.park_id:
            out.append(Violation("vehicle_park",
                                 f"{name}: автобус из парка {vehicle.park_id}, наряд парка "
                                 f"{duty.park_id}", (duty_id, vehicle_id)))
    return out


def _park_limits(day: Day, plan: Plan) -> list:
    out = []
    released = Counter(day.duties[d].park_id for d in plan.vehicles if d in day.duties)
    for park_id, count in released.items():
        park = day.parks[park_id]
        if park.state == "down":
            out.append(Violation("park_down",
                                 f"Парк {park.name} не работает, а на его наряды "
                                 f"поставлено {count} автобусов", (park_id,)))
        limit = park.release(day.day_type)
        if count > limit:
            out.append(Violation("release_limit",
                                 f"Парк {park.name}: выпущено {count}, лимит выпуска {limit}",
                                 (park_id,)))
    return out


def _driver_fits(day: Day, plan: Plan) -> list:
    out = []
    for shift_id, driver_id in plan.drivers.items():
        shift, driver = day.shifts.get(shift_id), day.drivers.get(driver_id)
        if shift is None or driver is None:
            continue
        duty = day.duties[shift.duty_id]
        name = f"Водитель {driver.tab_number} на смене {shift_id}"
        vehicle_id = plan.vehicles.get(duty.id)
        if vehicle_id is None:
            out.append(Violation("driver_without_vehicle",
                                 f"{name}: на наряде {duty.id} нет автобуса", (shift_id, driver_id)))
        cls = day.vehicles[vehicle_id].cls if vehicle_id in day.vehicles else duty.vehicle_class
        if cls not in driver.classes:
            out.append(Violation("driver_permit",
                                 f"{name}: нет допуска к классу {cls}", (shift_id, driver_id)))
        if driver.park_id != duty.park_id:
            out.append(Violation("driver_park",
                                 f"{name}: водитель из парка {driver.park_id}, наряд парка "
                                 f"{duty.park_id}", (shift_id, driver_id)))
        if driver.schedule != "work":
            out.append(Violation("driver_not_working",
                                 f"{name}: по графику не работает ({driver.schedule})",
                                 (shift_id, driver_id)))
        if driver.medical == "failed":
            out.append(Violation("driver_medical",
                                 f"{name}: не прошёл медосмотр", (shift_id, driver_id)))
    return out


def _driver_load(day: Day, plan: Plan, labor: dict) -> list:
    out = []
    shifts_of = defaultdict(list)
    for shift_id, driver_id in plan.drivers.items():
        if shift_id in day.shifts:
            shifts_of[driver_id].append(day.shifts[shift_id])
    for driver_id, shifts in shifts_of.items():
        tab = day.drivers[driver_id].tab_number if driver_id in day.drivers else driver_id
        shifts.sort(key=lambda s: s.start)
        if len(shifts) > labor["max_shifts_per_driver_day"]:
            out.append(Violation("driver_too_many_shifts",
                                 f"Водитель {tab}: {len(shifts)} смен за день, можно "
                                 f"{labor['max_shifts_per_driver_day']}",
                                 (driver_id, *(s.id for s in shifts))))
        for first, second in zip(shifts, shifts[1:]):
            if second.start < first.end:
                out.append(Violation("driver_overlap",
                                     f"Водитель {tab}: смены {first.id} и {second.id} "
                                     f"пересекаются по времени", (driver_id, first.id, second.id)))
        total = sum(s.length for s in shifts)
        limit = shift_limit(labor)
        if total > limit:
            note = ("" if labor["city_12h_agreed"] is not None else
                    "; до 12 ч можно, если с профсоюзом согласовано (п. 6 Приказа № 424)")
            out.append(Violation("driver_overtime",
                                 f"Водитель {tab}: {_hm(total)} за день, норма {_hm(limit)}{note}",
                                 (driver_id, *(s.id for s in shifts))))
    return out


def _hm(minutes_: int) -> str:
    return f"{minutes_ // 60} ч {minutes_ % 60:02d} мин"


def _unfilled_explained(day: Day, plan: Plan) -> list:
    """Всё незакрытое названо, и у всего названного есть причина."""
    out = []
    for item_id, reason in plan.unfilled.items():
        if reason not in REASONS:
            out.append(Violation("bad_reason",
                                 f"{item_id}: причина «{reason}» не из списка "
                                 f"{', '.join(REASONS)}", (item_id,)))
        if item_id in plan.vehicles or item_id in plan.drivers:
            out.append(Violation("unfilled_but_filled",
                                 f"{item_id} отмечен незакрытым, но на нём кто-то стоит",
                                 (item_id,)))
    for duty in day.duties.values():
        if duty.day_type != day.day_type:
            continue
        if duty.id not in plan.vehicles:
            if duty.id not in plan.unfilled:
                out.append(Violation("unexplained",
                                     f"Наряд {duty.id} без автобуса, а причина не названа",
                                     (duty.id,)))
            continue
        for shift in day.shifts_by_duty.get(duty.id, []):
            if shift.id not in plan.drivers and shift.id not in plan.unfilled:
                out.append(Violation("unexplained",
                                     f"Смена {shift.id} без водителя, а причина не названа",
                                     (shift.id,)))
    return out


def check_plan(day: Day, plan: Plan, labor: dict | None = None) -> list:
    """Все нарушения плана. Пустой список - план можно выпускать."""
    labor = labor or load_labor()
    unknown = _unknown(day, plan)
    if unknown:
        return unknown  # ссылки в никуда: дальше проверять нечего
    return (_vehicle_twice(day, plan) + _vehicle_fits(day, plan) + _park_limits(day, plan)
            + _driver_fits(day, plan) + _driver_load(day, plan, labor)
            + _unfilled_explained(day, plan))


def metrics(day: Day, plan: Plan) -> dict:
    """Сводка для отчёта: сколько закрыто, в том числе по важности маршрутов."""
    duties = [d for d in day.duties.values() if d.day_type == day.day_type]
    line = [d for d in duties if d.type == "line"]
    shifts = [s for d in duties if d.id in plan.vehicles for s in day.shifts_by_duty.get(d.id, [])]
    by_priority = defaultdict(lambda: [0, 0])
    for duty in line:
        stat = by_priority[day.routes[duty.route_id].priority]
        stat[1] += 1
        stat[0] += duty.id in plan.vehicles
    return {
        "line_duties": len(line),
        "line_filled": sum(d.id in plan.vehicles for d in line),
        "reserve_filled": sum(d.id in plan.vehicles for d in duties if d.type == "reserve"),
        "shifts_needed": len(shifts),
        "shifts_filled": sum(s.id in plan.drivers for s in shifts),
        "by_priority": {p: {"filled": f, "total": t} for p, (f, t) in sorted(by_priority.items())},
    }


def check_rest(series: list, labor: dict | None = None) -> list:
    """Отдых водителей на серии дней: список пар (день, план) по порядку дат.

    Проверяет то, что нельзя увидеть в одном дне (Приказ № 424):
    - отдых между сменами не меньше 11 ч, до 9 ч - не больше 3 раз
      между еженедельными отдыхами (п. 18);
    - отдых вместе с перерывом на питание - не меньше двух длин
      предыдущей смены (п. 18);
    - не больше 6 смен подряд без отдыха 45 ч (п. 19).
    Что было до первого дня серии, неизвестно: считаем, что перед ней
    у всех был еженедельный отдых.
    """
    labor = labor or load_labor()
    worked = defaultdict(list)
    for day, plan in series:
        base = date.fromisoformat(day.meta["date"]).toordinal() * 1440
        for shift_id, driver_id in plan.drivers.items():
            shift = day.shifts.get(shift_id)
            if shift is not None:
                worked[driver_id].append((base + shift.start, base + shift.end, shift.id))
    out = []
    for driver_id, shifts in worked.items():
        shifts.sort()
        reduced = in_row = 0
        for i, (start, end, shift_id) in enumerate(shifts):
            in_row += 1
            if in_row > labor["max_shifts_between_weekly_rests"]:
                out.append(Violation("weekly_rest",
                                     f"Водитель {driver_id}: смена {shift_id} - уже {in_row}-я "
                                     f"подряд без отдыха 45 ч, можно не больше "
                                     f"{labor['max_shifts_between_weekly_rests']} (п. 19)",
                                     (driver_id, shift_id)))
            if i + 1 == len(shifts):
                break
            next_start, _, next_id = shifts[i + 1]
            gap, work = next_start - end, end - start
            if gap >= labor["min_weekly_rest_min"]:
                reduced = in_row = 0
                continue
            need = labor["rest_to_work_ratio"] * work - labor["meal_break_assumed_min"]
            if gap < need:
                out.append(Violation("rest_ratio",
                                     f"Водитель {driver_id}: между {shift_id} и {next_id} отдых "
                                     f"{_hm(gap)}, а после смены {_hm(work)} нужно не меньше "
                                     f"{_hm(need)} (двойная смена минус перерыв на питание, п. 18)",
                                     (driver_id, shift_id, next_id)))
            if gap < labor["min_daily_rest_min"]:
                reduced += 1
                if gap < labor["min_daily_rest_reduced_min"] or reduced > labor["max_reduced_rests"]:
                    out.append(Violation("daily_rest",
                                         f"Водитель {driver_id}: между {shift_id} и {next_id} "
                                         f"отдых {_hm(gap)}; нужно 11 ч, сократить до 9 ч можно "
                                         f"не больше {labor['max_reduced_rests']} раз (п. 18)",
                                         (driver_id, shift_id, next_id)))
    return out
