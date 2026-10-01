"""
Меняющаяся часть данных: что происходит с автобусами и водителями день за днём.

Каждый день считается из вчерашнего:
- автобус в ремонте возвращается, когда ремонт закончился; исправный может
  сломаться или уйти на плановое ТО;
- водитель работает или отдыхает по графику 5 / 2, уходит в отпуск раз в год,
  может заболеть на несколько дней;
- утром у работающих проходит медосмотр.

Случайность на каждый день своя и зависит только от номера набора и даты,
поэтому один и тот же день всегда выглядит одинаково.
"""

from __future__ import annotations

import random
from datetime import date as Date

from .city import split_total
from .presets import CLASSES, DYNAMICS

INTERNAL_FIELDS = {"maintenance_offset", "rota_offset", "vacation_start"}


def day_type_of(day: Date) -> str:
    return "weekend" if day.weekday() >= 5 else "weekday"


def _rates(readiness: float) -> tuple:
    """Период ТО в днях и вероятность поломки в день под заданную готовность."""
    broken = 1 - readiness
    upkeep = broken * (1 - DYNAMICS["repair_share"])
    period = max(2, round(1 / upkeep)) if upkeep > 0 else None
    low, high = DYNAMICS["repair_days"]
    repair = broken * DYNAMICS["repair_share"]
    chance = repair / ((low + high) / 2 * readiness) if readiness > 0 else 1.0
    return period, chance


def initial_state(city: dict, seed: int) -> dict:
    """Первый день: неисправных ровно столько, сколько даёт тех. готовность."""
    rng = random.Random(f"start-{seed}")
    vehicles = {v["id"]: ["ok", 0] for v in city["vehicles"]}
    for park in city["parks"]:
        own = [v for v in city["vehicles"] if v["park_id"] == park["id"]]
        by_class = {cls: [v for v in own if v["class"] == cls] for cls in CLASSES}
        broken = split_total(round(len(own) * (1 - park["tech_readiness"])),
                             {cls: len(group) for cls, group in by_class.items()})
        for cls, group in by_class.items():
            chosen = rng.sample(group, broken[cls])
            repair = round(len(chosen) * DYNAMICS["repair_share"])
            for k, vehicle in enumerate(chosen):
                if k < repair:
                    vehicles[vehicle["id"]] = ["repair", rng.randint(1, DYNAMICS["repair_days"][1])]
                else:
                    vehicles[vehicle["id"]] = ["maintenance", 1]

    low, high = DYNAMICS["sick_days"]
    sick_share = DYNAMICS["sick_start"] * (low + high) / 2
    drivers = {d["id"]: rng.randint(1, high) if rng.random() < sick_share else 0
               for d in city["drivers"]}
    return {"vehicles": vehicles, "drivers": drivers}


def advance(city: dict, state: dict, day: Date, seed: int) -> dict:
    """Состояние на день day из состояния на предыдущий день."""
    rng = random.Random(f"vehicles-{seed}-{day.isoformat()}")
    readiness = {p["id"]: p["tech_readiness"] for p in city["parks"]}
    rates = {pid: _rates(r) for pid, r in readiness.items()}
    vehicles = {}
    for vehicle in city["vehicles"]:
        condition, left = state["vehicles"][vehicle["id"]]
        roll, length = rng.random(), rng.randint(*DYNAMICS["repair_days"])
        if condition != "ok":
            left -= 1
            if left <= 0:
                condition, left = "ok", 0
        if condition == "ok":
            period, chance = rates[vehicle["park_id"]]
            if period and (day.toordinal() + vehicle["maintenance_offset"]) % period == 0:
                condition, left = "maintenance", 1
            elif roll < chance:
                condition, left = "repair", length
        vehicles[vehicle["id"]] = [condition, left]

    rng = random.Random(f"drivers-{seed}-{day.isoformat()}")
    drivers = {}
    for driver in city["drivers"]:
        left = state["drivers"][driver["id"]]
        roll, length = rng.random(), rng.randint(*DYNAMICS["sick_days"])
        if left > 0:
            left -= 1
        if left == 0 and roll < DYNAMICS["sick_start"]:
            left = length
        drivers[driver["id"]] = left
    return {"vehicles": vehicles, "drivers": drivers}


def driver_status(driver: dict, day: Date, sick_left: int) -> str:
    year_day = day.timetuple().tm_yday - 1
    if (year_day - driver["vacation_start"]) % 365 < DYNAMICS["vacation_days"]:
        return "vacation"
    if sick_left > 0:
        return "sick"
    cycle = DYNAMICS["work_days"] + DYNAMICS["rest_days"]
    if (day.toordinal() + driver["rota_offset"]) % cycle >= DYNAMICS["work_days"]:
        return "day_off"
    return "work"


def snapshot(city: dict, state: dict, day: Date, seed: int, moment: str) -> dict:
    """Полный набор на один день в формате docs/CONTRACT.md (без meta)."""
    day_type = day_type_of(day)
    duties = [d for d in city["duties"] if d["day_type"] == day_type]
    duty_ids = {d["id"] for d in duties}

    vehicles = []
    for vehicle in city["vehicles"]:
        condition, left = state["vehicles"][vehicle["id"]]
        item = {k: v for k, v in vehicle.items() if k not in INTERNAL_FIELDS}
        item["condition"] = condition
        item["repair_days_left"] = left if condition != "ok" else None
        vehicles.append(item)

    rng = random.Random(f"medical-{seed}-{day.isoformat()}")
    passed, failed = DYNAMICS["medical_passed"], DYNAMICS["medical_failed"]
    drivers = []
    for driver in city["drivers"]:
        status = driver_status(driver, day, state["drivers"][driver["id"]])
        roll = rng.random()
        medical = None
        if status == "work":
            if moment == "plan":
                medical = "pending"
            else:
                medical = ("passed" if roll < passed else
                           "failed" if roll < passed + failed else "pending")
        item = {k: v for k, v in driver.items() if k not in INTERNAL_FIELDS}
        item["schedule"] = status
        item["medical"] = medical
        drivers.append(item)

    return {
        "parks": [dict(p) for p in city["parks"]],
        "routes": [dict(r) for r in city["routes"]],
        "duties": [dict(d) for d in duties],
        "shifts": [dict(s) for s in city["shifts"] if s["duty_id"] in duty_ids],
        "vehicles": vehicles,
        "drivers": drivers,
    }
