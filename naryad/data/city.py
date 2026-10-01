"""
Постоянная часть данных: город, парки, маршруты, наряды, автобусы, водители.

Всё, что здесь создаётся, не меняется день ото дня: расписание нарядов,
состав парков, закрепления, допуски. Что меняется по дням (исправность,
выход водителей, медосмотр), считает days.py.

Одинаковые вводные и номер набора дают один и тот же город.
"""

from __future__ import annotations

import copy
import math
import random

from .names import full_name
from .presets import CITY_PARKS, CLASSES, DYNAMICS, PARK7, PRESETS

PLATE_LETTERS = "АВЕКМНОРСТУХ"
PLATE_REGIONS = ("78", "98", "178", "198")

BIG_CLASS_FILL = 0.85
SPEED_KMH = 18
LAYOVER_MIN = 20
EXTRA_PERMIT_BOOST = 1.15
DRIVERS_PER_VEHICLE = 2
DAY_TYPES = ("weekday", "weekend")
DAY_CODES = {"weekday": "WD", "weekend": "WE"}

# Окно начала и длительность наряда по числу смен, минуты, шаг 5.
DUTY_TIMES = {
    1: ((330, 540), (480, 570)),
    2: ((300, 420), (900, 1020)),
    3: ((290, 340), (1140, 1230)),
}


def hhmm(minutes: int) -> str:
    """Минуты от начала суток -> 'ЧЧ:ММ'. После полуночи часы идут дальше 24."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hhmm(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def split_total(total: int, weights: dict) -> dict:
    """Делит целое число пропорционально весам, сумма сохраняется точно."""
    keys = list(weights)
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        return {key: 0 for key in keys}
    exact = {key: total * weights[key] / weight_sum for key in keys}
    result = {key: int(exact[key]) for key in keys}
    rest = total - sum(result.values())
    for key in sorted(keys, key=lambda k: exact[k] - result[k], reverse=True)[:rest]:
        result[key] += 1
    return result


def _step(rng: random.Random, low: int, high: int) -> int:
    return rng.randrange(low, high + 1, 5)


def _park_number(park_id: str) -> int:
    return int("".join(ch for ch in park_id if ch.isdigit()))


# --- Настройки -> подробное описание каждого парка -------------------------

def resolve(preset: str = "case", **overrides) -> dict:
    """Готовая настройка + правки вводных (park_count, release_per_park, ...)."""
    if preset not in PRESETS:
        raise ValueError(f"Нет настройки «{preset}», есть: {', '.join(PRESETS)}")
    config = copy.deepcopy(PRESETS[preset])
    config["preset"] = preset
    for key, value in overrides.items():
        if value is None:
            continue
        if key not in config:
            raise ValueError(f"Настройка «{preset}» не принимает {key}")
        config[key] = value
    return config


def _expected_reserve(spec: dict, day_type: str) -> dict:
    """Резерв по классам - пропорционально ожидаемому запасу исправных машин."""
    fleet = _fleet_by_class(spec)
    need: dict = {}
    for row in spec["routes"]:
        cls = row[4] if len(row) > 4 else None
        if cls:
            need[cls] = need.get(cls, 0) + row[1 if day_type == "weekday" else 2]
    spare = {cls: max(0.0, count * spec["tech_readiness"] - need.get(cls, 0))
             for cls, count in fleet.items()}
    if not any(spare.values()):
        spare = dict(fleet)
    return split_total(spec["reserve"][day_type], spare)


def _fleet_by_class(spec: dict) -> dict:
    counts: dict = {}
    for _, cls, _, count in spec["fleet"]:
        counts[cls] = counts.get(cls, 0) + count
    return counts


def _route_classes(spec: dict) -> list:
    """Для реального парка: самым загруженным маршрутам - самый вместительный класс."""
    rows = spec["routes"]
    fleet = _fleet_by_class(spec)
    present = [cls for cls in reversed(CLASSES) if fleet.get(cls)]
    order = sorted(range(len(rows)), key=lambda i: -rows[i][1])
    class_of: dict = {}
    for cls in present[:-1]:
        cap = BIG_CLASS_FILL * fleet[cls] * spec["tech_readiness"]
        used = 0
        for i in order:
            need = rows[i][1]
            if i in class_of or need == 0:
                continue
            if used + need <= cap:
                class_of[i] = cls
                used += need
    for i in order:
        class_of.setdefault(i, present[-1])
    return [tuple(row[:4]) + (class_of[i],) for i, row in enumerate(rows)]


def _models_like_park7(cls: str, count: int) -> list:
    """Марки по пропорциям парка №7; у среднего класса там нет образца."""
    sample = [(m, f, n) for m, c, f, n in PARK7["fleet"] if c == cls]
    if not sample:
        return [("средний класс, модель не задана", cls, "diesel", count)]
    split = split_total(count, {i: n for i, (_, _, n) in enumerate(sample)})
    return [(sample[i][0], cls, sample[i][1], k) for i, k in split.items() if k]


def _synthetic_specs(config: dict, rng: random.Random) -> list:
    count = config["park_count"]
    if not 1 <= count <= len(CITY_PARKS):
        raise ValueError(f"Парков можно от 1 до {len(CITY_PARKS)}")
    release = config["release_per_park"]
    mix = {cls: config["class_mix"].get(cls, 0) for cls in CLASSES}
    routes_by_park = split_total(config["routes_total"], {i: 1 for i in range(count)})
    reserve_total = round(release * config["reserve_share"])
    factor = config["weekend_factor"]

    specs = []
    previous = {cls: 0 for cls in CLASSES}
    for i, (park_id, name, address) in enumerate(CITY_PARKS[:count]):
        # Нарастающий итог: в каждом парке ровно release, по городу - ровно доли.
        total = split_total((i + 1) * release, mix)
        by_class = {cls: total[cls] - previous[cls] for cls in CLASSES}
        previous = total
        reserve = split_total(reserve_total, by_class)
        line = {cls: by_class[cls] - reserve[cls] for cls in CLASSES}
        present = [cls for cls in CLASSES if line[cls] > 0]
        n_routes = routes_by_park[i]
        if n_routes < len(present):
            raise ValueError(f"{name}: маршрутов {n_routes}, а классов {len(present)}")
        per_class = split_total(n_routes - len(present), {c: line[c] for c in present})

        rows = []
        for cls in present:
            k = per_class[cls] + 1
            if line[cls] < k:
                raise ValueError(f"{name}: нарядов класса {cls} меньше, чем маршрутов")
            weights = {j: rng.lognormvariate(0, 0.6) for j in range(k)}
            sizes = split_total(line[cls] - k, weights)
            for j in range(k):
                weekday = sizes[j] + 1
                rows.append([None, weekday, round(weekday * factor),
                             round(rng.uniform(8, 25), 2), cls])
        rows.sort(key=lambda row: -row[1])
        prefix = f"П{_park_number(park_id)}"
        for j, row in enumerate(rows):
            row[0] = f"{prefix}-{j + 1:02d}"

        reserve_weekend = round(reserve_total * factor)
        weekend_line = sum(row[2] for row in rows)
        fleet = []
        for cls in CLASSES:
            if by_class[cls]:
                fleet += _models_like_park7(cls, math.ceil(by_class[cls] / config["release_coef"]))
        release_by_day = {"weekday": release, "weekend": weekend_line + reserve_weekend}
        specs.append({
            "id": park_id,
            "name": name,
            "address": address,
            "list_count": sum(row[3] for row in fleet),
            "tech_readiness": config["tech_readiness"],
            "release": release_by_day,
            "reserve": {"weekday": reserve_total, "weekend": reserve_weekend},
            "shift_mix": {day: split_total(release_by_day[day], config["shift_mix"])
                          for day in DAY_TYPES},
            "fleet": fleet,
            "routes": [tuple(row) for row in rows],
        })
    return specs


def park_specs(config: dict, rng: random.Random) -> list:
    if config["kind"] == "explicit":
        specs = copy.deepcopy(config["parks"])
        for spec in specs:
            spec["routes"] = _route_classes(spec)
        return specs
    return _synthetic_specs(config, rng)


# --- Подробное описание парка -> записи -----------------------------------

def _park(spec: dict) -> dict:
    return {
        "id": spec["id"],
        "name": spec["name"],
        "address": spec.get("address"),
        "lat": spec.get("lat"),
        "lon": spec.get("lon"),
        "state": "working",
        "list_count": spec["list_count"],
        "tech_readiness": spec["tech_readiness"],
        "release_weekday": spec["release"]["weekday"],
        "release_weekend": spec["release"]["weekend"],
        "reserve_weekday": spec["reserve"]["weekday"],
        "reserve_weekend": spec["reserve"]["weekend"],
    }


def _routes(spec: dict, rng: random.Random) -> list:
    rows = spec["routes"]
    busy = sorted((i for i in range(len(rows)) if rows[i][1] > 0), key=lambda i: -rows[i][1])
    third = len(busy) / 3
    priority = {i: 1 if rank < third else 2 if rank < 2 * third else 3
                for rank, i in enumerate(busy)}
    routes = []
    for i, (number, weekday, weekend, length, cls) in enumerate(rows):
        source = "справка" if spec.get("source") else "синтетика"
        if length is None:
            length = round(rng.uniform(10, 25), 2)
            source = "синтетика"
        turnaround = 2 * length / SPEED_KMH * 60 + LAYOVER_MIN
        routes.append({
            "id": f"{spec['id']}-R{i + 1:02d}",
            "park_id": spec["id"],
            "number": number,
            "allowed_classes": [cls],
            "priority": priority.get(i, 3),
            "length_km": length,
            "length_source": source,
            "turnaround_min": int(round(turnaround / 5) * 5),
            "duties_weekday": weekday,
            "duties_weekend": weekend,
        })
    return routes


def _plate(rng: random.Random, used: set) -> str:
    while True:
        plate = (f"{rng.choice(PLATE_LETTERS)}{rng.choice(PLATE_LETTERS)} "
                 f"{rng.randrange(1, 1000):03d} {rng.choice(PLATE_REGIONS)}")
        if plate not in used:
            used.add(plate)
            return plate


def _vehicles(spec: dict, routes: list, rng: random.Random, used_plates: set) -> list:
    number = _park_number(spec["id"])
    vehicles = []
    for model, cls, fuel, count in spec["fleet"]:
        for _ in range(count):
            n = len(vehicles) + 1
            vehicles.append({
                "id": f"{spec['id']}-V{n:04d}",
                "board_number": str(number * 1000 + n),
                "plate": _plate(rng, used_plates),
                "model": model,
                "class": cls,
                "fuel": fuel,
                "park_id": spec["id"],
                "home_route_id": None,
                "maintenance_offset": rng.randrange(1000),
            })
    # Свой маршрут: каждому маршруту столько машин его класса, сколько у него
    # нарядов в будни. Оставшиеся - без маршрута.
    for cls in CLASSES:
        pool = [v for v in vehicles if v["class"] == cls]
        rng.shuffle(pool)
        own = sorted((r for r in routes if r["allowed_classes"][0] == cls),
                     key=lambda r: -r["duties_weekday"])
        for route in own:
            for _ in range(route["duties_weekday"]):
                if not pool:
                    break
                pool.pop()["home_route_id"] = route["id"]
    return vehicles


def _duties(spec: dict, routes: list, day_type: str, rng: random.Random) -> tuple:
    code = DAY_CODES[day_type]
    plan = []  # (id, тип, маршрут, класс)
    for route in routes:
        for k in range(route[f"duties_{day_type}"]):
            plan.append((f"{route['id']}-{code}{k + 1:02d}", "line", route["id"],
                         route["allowed_classes"][0]))
    reserve = _expected_reserve(spec, day_type)
    n = 0
    for cls in CLASSES:
        for _ in range(reserve.get(cls, 0)):
            n += 1
            plan.append((f"{spec['id']}-RES-{code}{n:02d}", "reserve", None, cls))

    mix = spec["shift_mix"][day_type]
    shift_counts = [k for k in (1, 2, 3) for _ in range(mix[k])]
    if len(shift_counts) != len(plan):
        raise ValueError(
            f"{spec['name']}: нарядов по видам смен {len(shift_counts)}, а нарядов на "
            f"маршрутах и в резерве {len(plan)} ({day_type})")
    rng.shuffle(shift_counts)

    duties, shifts = [], []
    for (duty_id, kind, route_id, cls), k in zip(plan, shift_counts):
        (start_lo, start_hi), (dur_lo, dur_hi) = DUTY_TIMES[k]
        start = _step(rng, start_lo, start_hi)
        end = start + _step(rng, dur_lo, dur_hi)
        duties.append({
            "id": duty_id,
            "park_id": spec["id"],
            "type": kind,
            "route_id": route_id,
            "vehicle_class": cls,
            "day_type": day_type,
            "start": hhmm(start),
            "end": hhmm(end),
            "shift_count": k,
        })
        part = (end - start) // k // 5 * 5
        for i in range(k):
            shifts.append({
                "id": f"{duty_id}-S{i + 1}",
                "duty_id": duty_id,
                "order": i + 1,
                "start": hhmm(start + i * part),
                "end": hhmm(end if i == k - 1 else start + (i + 1) * part),
                "start_place": "park" if i == 0 or kind == "reserve" else "line",
            })
    return duties, shifts


def _permit_tops(spec: dict, total: int, rng: random.Random) -> list:
    """Высший класс допуска у каждого водителя: доли чуть выше долей парка."""
    fleet = _fleet_by_class(spec)
    present = [cls for cls in reversed(CLASSES) if fleet.get(cls)]
    fleet_total = sum(fleet.values())
    tops, left = [], total
    for cls in present[:-1]:
        k = min(left, round(total * fleet[cls] / fleet_total * EXTRA_PERMIT_BOOST))
        tops += [cls] * k
        left -= k
    tops += [present[-1]] * left
    rng.shuffle(tops)
    return tops


def _drivers(spec: dict, vehicles: list, rng: random.Random) -> list:
    weekday_shifts = sum(k * n for k, n in spec["shift_mix"]["weekday"].items())
    total = round(weekday_shifts * DYNAMICS["drivers_per_shift"])
    tops = _permit_tops(spec, total, rng)
    number = _park_number(spec["id"])
    cycle = DYNAMICS["work_days"] + DYNAMICS["rest_days"]
    drivers = []
    for n in range(total):
        drivers.append({
            "id": f"{spec['id']}-D{n + 1:04d}",
            "tab_number": f"{number:02d}{n + 1:04d}",
            "full_name": full_name(rng),
            "park_id": spec["id"],
            "classes": list(CLASSES[:CLASSES.index(tops[n]) + 1]),
            "home_vehicle_id": None,
            "rota_offset": rng.randrange(cycle),
            "vacation_start": rng.randrange(365),
        })

    # Закрепление: сначала водители ровно с этим допуском, потом с бо́льшим.
    pools: dict = {}
    for driver in drivers:
        pools.setdefault(driver["classes"][-1], []).append(driver)
    attached = [v for v in vehicles if v["home_route_id"]]
    rng.shuffle(attached)
    for vehicle in attached:
        for _ in range(DRIVERS_PER_VEHICLE):
            for cls in CLASSES[CLASSES.index(vehicle["class"]):]:
                if pools.get(cls):
                    pools[cls].pop()["home_vehicle_id"] = vehicle["id"]
                    break
    return drivers


def build_city(config: dict, seed: int = 1) -> dict:
    """Постоянная часть: всё, что не меняется день ото дня."""
    rng = random.Random(f"city-{seed}")
    city = {"parks": [], "routes": [], "duties": [], "shifts": [],
            "vehicles": [], "drivers": []}
    used_plates: set = set()
    for spec in park_specs(config, rng):
        routes = _routes(spec, rng)
        vehicles = _vehicles(spec, routes, rng, used_plates)
        city["parks"].append(_park(spec))
        city["routes"] += routes
        city["vehicles"] += vehicles
        for day_type in DAY_TYPES:
            duties, shifts = _duties(spec, routes, day_type, rng)
            city["duties"] += duties
            city["shifts"] += shifts
        city["drivers"] += _drivers(spec, vehicles, rng)
    return city
