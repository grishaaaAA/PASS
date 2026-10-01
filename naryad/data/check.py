"""
Проверка набора данных перед тем, как отдать его движку.

Ошибка - данные противоречат сами себе, движку их давать нельзя.
Предупреждение - данные целы, но что-то расходится с планом или
заведомо не хватает ресурсов (это бывает и в жизни).

Одна и та же проверка работает и для генератора, и для загрузки CSV.
"""

from __future__ import annotations

import re

from .presets import CLASSES

# Поля каждой сущности: имя, тип, может ли быть пустым.
# Типы: str, int, float, list (список строк).
SCHEMA = {
    "parks": [
        ("id", "str", False), ("name", "str", False),
        ("address", "str", True), ("lat", "float", True), ("lon", "float", True),
        ("state", "str", False), ("list_count", "int", False),
        ("tech_readiness", "float", False),
        ("release_weekday", "int", False), ("release_weekend", "int", False),
        ("reserve_weekday", "int", False), ("reserve_weekend", "int", False),
    ],
    "routes": [
        ("id", "str", False), ("park_id", "str", False), ("number", "str", False),
        ("allowed_classes", "list", False), ("priority", "int", False),
        ("length_km", "float", False), ("length_source", "str", False),
        ("turnaround_min", "int", False),
        ("duties_weekday", "int", False), ("duties_weekend", "int", False),
    ],
    "duties": [
        ("id", "str", False), ("park_id", "str", False), ("type", "str", False),
        ("route_id", "str", True), ("vehicle_class", "str", False),
        ("day_type", "str", False), ("start", "str", False), ("end", "str", False),
        ("shift_count", "int", False),
    ],
    "shifts": [
        ("id", "str", False), ("duty_id", "str", False), ("order", "int", False),
        ("start", "str", False), ("end", "str", False), ("start_place", "str", False),
    ],
    "vehicles": [
        ("id", "str", False), ("board_number", "str", False), ("plate", "str", False),
        ("model", "str", False), ("class", "str", False), ("fuel", "str", False),
        ("park_id", "str", False), ("condition", "str", False),
        ("home_route_id", "str", True),
    ],
    "drivers": [
        ("id", "str", False), ("tab_number", "str", False), ("full_name", "str", False),
        ("park_id", "str", False), ("classes", "list", False),
        ("home_vehicle_id", "str", True), ("schedule", "str", False),
        ("medical", "str", True),
    ],
}

ENUMS = {
    ("parks", "state"): {"working", "emergency", "down"},
    ("routes", "allowed_classes"): set(CLASSES),
    ("duties", "type"): {"line", "reserve"},
    ("duties", "vehicle_class"): set(CLASSES),
    ("duties", "day_type"): {"weekday", "weekend"},
    ("shifts", "start_place"): {"park", "line"},
    ("vehicles", "class"): set(CLASSES),
    ("vehicles", "fuel"): {"gas", "diesel", "electric"},
    ("vehicles", "condition"): {"ok", "repair", "maintenance", "accident"},
    ("drivers", "classes"): set(CLASSES),
    ("drivers", "schedule"): {"work", "day_off"},
    ("drivers", "medical"): {"passed", "failed", "pending"},
}

TIME_RE = re.compile(r"^\d{2}:[0-5]\d$")
TYPES = {"str": str, "int": int, "float": (int, float), "list": list}


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def _fields(data: dict, errors: list) -> None:
    for entity, fields in SCHEMA.items():
        for item in data.get(entity, []):
            name = item.get("id", "?")
            for field, kind, nullable in fields:
                if field not in item:
                    errors.append(f"{entity} {name}: нет поля {field}")
                    continue
                value = item[field]
                if value is None:
                    if not nullable:
                        errors.append(f"{entity} {name}: поле {field} пустое")
                    continue
                if not isinstance(value, TYPES[kind]) or isinstance(value, bool):
                    errors.append(f"{entity} {name}: поле {field} должно быть {kind}")
                    continue
                allowed = ENUMS.get((entity, field))
                if allowed is not None:
                    values = value if kind == "list" else [value]
                    bad = [v for v in values if v not in allowed]
                    if bad:
                        errors.append(f"{entity} {name}: {field} = {bad}, "
                                      f"допустимо {sorted(allowed)}")


def check(data: dict) -> dict:
    errors: list = []
    warnings: list = []

    for entity in SCHEMA:
        if not isinstance(data.get(entity), list):
            errors.append(f"нет списка {entity}")
    if errors:
        return {"errors": errors, "warnings": warnings}

    _fields(data, errors)
    if errors:
        return {"errors": errors, "warnings": warnings}

    index = {}
    for entity in SCHEMA:
        index[entity] = {}
        for item in data[entity]:
            if item["id"] in index[entity]:
                errors.append(f"{entity}: номер {item['id']} повторяется")
            index[entity][item["id"]] = item
    parks, routes = index["parks"], index["routes"]
    duties, vehicles = index["duties"], index["vehicles"]

    def ref(entity: str, item: dict, field: str, target: dict) -> None:
        value = item[field]
        if value is not None and value not in target:
            errors.append(f"{entity} {item['id']}: {field} = {value}, такого нет")

    for route in data["routes"]:
        ref("routes", route, "park_id", parks)
    for vehicle in data["vehicles"]:
        ref("vehicles", vehicle, "park_id", parks)
        ref("vehicles", vehicle, "home_route_id", routes)
    for driver in data["drivers"]:
        ref("drivers", driver, "park_id", parks)
        ref("drivers", driver, "home_vehicle_id", vehicles)
        vehicle = vehicles.get(driver["home_vehicle_id"])
        if vehicle and vehicle["class"] not in driver["classes"]:
            errors.append(f"drivers {driver['id']}: закреплён за "
                          f"{vehicle['id']}, но нет допуска к классу {vehicle['class']}")
        if driver["schedule"] == "day_off" and driver["medical"] is not None:
            errors.append(f"drivers {driver['id']}: выходной, но указан медосмотр")

    shifts_of: dict = {}
    for shift in data["shifts"]:
        ref("shifts", shift, "duty_id", duties)
        shifts_of.setdefault(shift["duty_id"], []).append(shift)

    for duty in data["duties"]:
        did = duty["id"]
        ref("duties", duty, "park_id", parks)
        if duty["type"] == "line":
            if duty["route_id"] is None:
                errors.append(f"duties {did}: линейный наряд без маршрута")
            else:
                ref("duties", duty, "route_id", routes)
                route = routes.get(duty["route_id"])
                if route and duty["vehicle_class"] not in route["allowed_classes"]:
                    errors.append(f"duties {did}: класс {duty['vehicle_class']} "
                                  f"не допущен на маршрут {route['number']}")
        elif duty["route_id"] is not None:
            errors.append(f"duties {did}: у резервного наряда не должно быть маршрута")
        if not 1 <= duty["shift_count"] <= 3:
            errors.append(f"duties {did}: смен должно быть от 1 до 3")
        times = [duty["start"], duty["end"]]
        if not all(TIME_RE.match(t) for t in times):
            errors.append(f"duties {did}: время должно быть ЧЧ:ММ")
            continue
        own = sorted(shifts_of.get(did, []), key=lambda s: s["order"])
        if len(own) != duty["shift_count"]:
            errors.append(f"duties {did}: смен {len(own)}, а указано {duty['shift_count']}")
            continue
        if not all(TIME_RE.match(s["start"]) and TIME_RE.match(s["end"]) for s in own):
            errors.append(f"duties {did}: время смен должно быть ЧЧ:ММ")
            continue
        point = _minutes(duty["start"])
        for shift in own:
            if _minutes(shift["start"]) != point or _minutes(shift["end"]) <= point:
                errors.append(f"duties {did}: смены идут не встык или пустые")
                break
            point = _minutes(shift["end"])
        else:
            if point != _minutes(duty["end"]):
                errors.append(f"duties {did}: смены не доходят до конца наряда")

    day_type = data.get("meta", {}).get("day_type")
    for park in data["parks"]:
        pid = park["id"]
        own_vehicles = [v for v in data["vehicles"] if v["park_id"] == pid]
        if len(own_vehicles) != park["list_count"]:
            errors.append(f"парк {pid}: по документам {park['list_count']} автобусов, "
                          f"в списке {len(own_vehicles)}")
        own_duties = [d for d in data["duties"] if d["park_id"] == pid]
        if day_type in ("weekday", "weekend"):
            line = sum(d["type"] == "line" for d in own_duties)
            reserve = len(own_duties) - line
            plan_reserve = park[f"reserve_{day_type}"]
            plan_release = park[f"release_{day_type}"]
            if reserve != plan_reserve:
                warnings.append(f"парк {pid}: резерв {reserve}, по плану {plan_reserve}")
            if line + reserve != plan_release:
                warnings.append(f"парк {pid}: нарядов {line + reserve}, "
                                f"выпуск по плану {plan_release}")
            for route in data["routes"]:
                if route["park_id"] != pid:
                    continue
                have = sum(d["route_id"] == route["id"] for d in own_duties)
                if have != route[f"duties_{day_type}"]:
                    warnings.append(f"маршрут {route['number']}: нарядов {have}, "
                                    f"по плану {route[f'duties_{day_type}']}")
        for cls in CLASSES:
            have = sum(v["class"] == cls and v["condition"] == "ok" for v in own_vehicles)
            need = sum(d["vehicle_class"] == cls for d in own_duties)
            if need > have:
                warnings.append(f"парк {pid}: класс {cls} - исправных {have}, "
                                f"нужно {need}, не хватает {need - have}")
        duty_ids = {d["id"] for d in own_duties}
        need_shifts = sum(s["duty_id"] in duty_ids for s in data["shifts"])
        working = sum(d["park_id"] == pid and d["schedule"] == "work"
                      for d in data["drivers"])
        if working < need_shifts:
            warnings.append(f"парк {pid}: смен {need_shifts}, водителей по графику "
                            f"{working}, не хватает {need_shifts - working}")

    return {"errors": errors, "warnings": warnings}
