"""
Проверка набора данных перед тем, как отдать его движку.

Ошибка - данные противоречат сами себе, движку их давать нельзя.
Предупреждение - данные целы, но что-то расходится с планом или
заведомо не хватает ресурсов (это бывает и в жизни).

Одна и та же проверка работает и для генератора, и для загрузки CSV.
"""

from __future__ import annotations

import re
from datetime import date as Date

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
        ("repair_days_left", "int", True), ("home_route_id", "str", True),
    ],
    "drivers": [
        ("id", "str", False), ("tab_number", "str", False), ("full_name", "str", False),
        ("park_id", "str", False), ("classes", "list", False),
        ("home_vehicle_id", "str", True), ("schedule", "str", False),
        ("medical", "str", True),
    ],
}

# Необязательные поля: их может не быть вовсе. Нет поля - нет ограничения,
# и данные без них работают как раньше (генератор их не пишет).
# vehicles.make - марка автобуса; без неё марка берётся из начала model (модель
#   после марки отрезается: «ЛиАЗ-5292.65» - это ЛиАЗ, naryad.core.model.make_of).
# drivers.makes - марки, на которых водитель обкатан: у перевозчика допуск
#   даётся на марку (МАЗ, НЕФАЗ, ЛиАЗ) на всех её модификациях.
# drivers.routes - маршруты, на которые водитель допущен (коды маршрутов из routes.id).
OPTIONAL = {
    "vehicles": [("make", "str")],
    "drivers": [("makes", "list"), ("routes", "list")],
}
MAKE_FIELDS = {"make", "makes"}  # в них марка: сравнивается через naryad.core.model.normal_make

# Обязательные поля meta: без даты и типа дня день нельзя ни спланировать,
# ни показать. moment и format_version не обязательны, но если есть - проверяются.
META_ENUMS = {
    "day_type": {"weekday", "weekend"},
    "moment": {"plan", "morning"},
}

ENUMS = {
    ("parks", "state"): {"working", "emergency", "down"},
    ("routes", "priority"): {1, 2, 3},  # веса важности движка: naryad/solve/vehicles.py
    ("routes", "allowed_classes"): set(CLASSES),
    ("duties", "type"): {"line", "reserve"},
    ("duties", "vehicle_class"): set(CLASSES),
    ("duties", "day_type"): {"weekday", "weekend"},
    ("shifts", "start_place"): {"park", "line"},
    ("vehicles", "class"): set(CLASSES),
    ("vehicles", "fuel"): {"gas", "diesel", "electric"},
    ("vehicles", "condition"): {"ok", "repair", "maintenance", "accident"},
    ("drivers", "classes"): set(CLASSES),
    ("drivers", "schedule"): {"work", "day_off", "sick", "vacation"},
    ("drivers", "medical"): {"passed", "failed", "pending"},
}

TIME_RE = re.compile(r"^\d{2}:[0-5]\d$")
TYPES = {"str": str, "int": int, "float": (int, float), "list": list}


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def _meta(data: dict, errors: list) -> None:
    """Блок meta: дата и тип дня обязательны, остальное - если есть."""
    meta = data.get("meta")
    if not isinstance(meta, dict):
        errors.append("нет блока meta: нужны дата дня и тип дня")
        return
    date = meta.get("date")
    if not isinstance(date, str) or not date:
        errors.append("meta: нет даты дня (поле date в виде ГГГГ-ММ-ДД)")
    else:
        try:
            Date.fromisoformat(date)
        except ValueError:
            errors.append(f"meta: date = {date!r}, нужно ГГГГ-ММ-ДД")
    for field, allowed in META_ENUMS.items():
        value = meta.get(field)
        if field == "day_type" and value is None:
            errors.append("meta: нет типа дня (поле day_type: weekday или weekend)")
            continue
        if value is not None and value not in allowed:
            errors.append(f"meta: {field} = {value!r}, допустимо {sorted(allowed)}")


def _fields(data: dict, errors: list) -> None:
    for entity, fields in SCHEMA.items():
        for position, item in enumerate(data.get(entity, []), 1):
            if not isinstance(item, dict):
                errors.append(f"{entity}: запись {position} не объект, а {type(item).__name__}")
                continue
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
                        shown = bad if kind == "list" else bad[0]
                        errors.append(f"{entity} {name}: {field} = {shown!r}, "
                                      f"допустимо {sorted(allowed)}")


def _optional(data: dict, errors: list) -> None:
    """Необязательные поля: если есть - нужного вида; пустой список - ошибка.

    Пустой список допусков читался бы двояко: «ограничений нет» или «не
    допущен никуда». Поэтому его не принимаем: нет ограничения - уберите поле.
    По той же причине марка без единой буквы («-», «?», «5292») - ошибка, а
    не марка: водитель с такой «маркой» молча не попал бы ни на один автобус.
    """
    from naryad.core.model import normal_make  # model сама импортирует check
    for entity, fields in OPTIONAL.items():
        for position, item in enumerate(data.get(entity, []), 1):
            if not isinstance(item, dict):
                continue
            name = item.get("id", "?")
            for field, kind in fields:
                value = item.get(field)
                if value is None:
                    continue
                if kind == "str" and not isinstance(value, str):
                    errors.append(f"{entity} {name}: поле {field} должно быть str")
                    continue
                if kind == "list":
                    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                        errors.append(f"{entity} {name}: поле {field} - список непустых строк")
                        continue
                    if not value:
                        errors.append(f"{entity} {name}: поле {field} пустое - нет ограничения, "
                                      f"тогда уберите поле совсем")
                        continue
                if field in MAKE_FIELDS:
                    for text in value if kind == "list" else [value]:
                        if not normal_make(text):
                            errors.append(f"{entity} {name}: в поле {field} {text!r} - нет марки. Марка "
                                          f"пишется буквами («ЛиАЗ», «МАЗ»), нет марки - уберите поле совсем")


def _some(ids: list, shown: int = 5) -> str:
    """«P07-D0001, P07-D0002 и ещё 12»: длинный список людей в сообщении не нужен."""
    head = ", ".join(ids[:shown])
    return head + (f" и ещё {len(ids) - shown}" if len(ids) > shown else "")


def _permits(data: dict, vehicles: dict, routes: dict, errors: list, warnings: list) -> None:
    """Допуск водителей по марке и маршрутам против того, что есть в дне.

    Закреплённый автобус марки, на которой водитель не обкатан, - ошибка:
    данные противоречат сами себе. Марка или маршрут, которых в дне нет, -
    предупреждение: такой пункт допуска не действует. Если у водителя не
    нашлось ни одного пункта, он не попадёт ни на один автобус (марки) или
    ни на один линейный наряд (маршруты) - это говорится прямо, а не «допуск
    ничего не даёт»: чаще всего дело в написании. Сообщения собраны по марке
    и маршруту, а не по водителю: опечатка в реестре задевает сотни человек,
    и список из сотни одинаковых строк вытеснил бы остальные предупреждения.
    """
    from naryad.core.model import make_of, make_part, normal_make  # model сама импортирует check
    day_makes = {normal_make(make_of(v)) for v in data["vehicles"]}
    unknown_make, unknown_route, several = {}, {}, {}
    no_make, no_route = [], []
    for driver in data["drivers"]:
        name, makes, own_routes = driver["id"], driver.get("makes") or [], driver.get("routes") or []
        vehicle = vehicles.get(driver["home_vehicle_id"])
        if vehicle and makes and normal_make(make_of(vehicle)) not in {normal_make(m) for m in makes}:
            errors.append(f"drivers {name}: закреплён за {vehicle['id']} марки "
                          f"{make_of(vehicle) or 'без марки'}, но на этой марке не обкатан")
        for text in dict.fromkeys(makes):
            if normal_make(text) not in day_makes:  # «Скания» и «скания» - одна строка сообщения
                unknown_make.setdefault(normal_make(text), (text, []))[1].append(name)
            others = {normal_make(w) for w in text.split()[1:]} - {"", normal_make(text)}
            if others & day_makes:   # «МАЗ ЛиАЗ»: вторая марка через пробел пропала бы молча
                several.setdefault(text, []).append(name)
        if makes and not {normal_make(m) for m in makes} & day_makes:
            no_make.append(name)
        for route_id in dict.fromkeys(own_routes):
            if route_id not in routes:
                unknown_route.setdefault(route_id, []).append(name)
        if own_routes and not set(own_routes) & set(routes):
            no_route.append(name)
        home_route = routes.get(vehicle["home_route_id"]) if vehicle and vehicle["home_route_id"] else None
        if own_routes and home_route and home_route["id"] not in own_routes:
            warnings.append(f"drivers {name}: закреплён за {vehicle['id']}, а этот автобус закреплён за "
                            f"маршрутом {home_route['number']}, к которому водитель не допущен")
    nameless = [v["id"] for v in data["vehicles"] if not normal_make(make_of(v))]
    if nameless and any(d.get("makes") for d in data["drivers"]):
        warnings.append(f"vehicles: у автобусов не указана марка (нет ни make, ни марки в начале model), "
                        f"а у водителей есть допуск по марке - водители со списком марок на эти автобусы "
                        f"не попадут: {_some(nameless)}")
    for text, names in unknown_make.values():
        warnings.append(f"drivers: в допуске марка «{text}», а автобусов этой марки в дне нет "
                        f"(водители {_some(names)})")
    for text, names in several.items():
        warnings.append(f"drivers: в допуске «{text}» - несколько марок через пробел? Учтена только "
                        f"первая, {make_part(text)}: марки разделяются запятой или | (водители {_some(names)})")
    if no_make:
        warnings.append(f"drivers: ни одной марки из допуска нет у автобусов дня, эти водители не попадут "
                        f"ни на один автобус: {_some(no_make)}")
    for route_id, names in unknown_route.items():
        warnings.append(f"drivers: в допуске маршрут {route_id}, а такого маршрута в дне нет "
                        f"(водители {_some(names)})")
    if no_route:
        warnings.append(f"drivers: ни одного маршрута из допуска нет в дне, эти водители на линию не "
                        f"попадут, только в резерв: {_some(no_route)}")


def check(data: dict) -> dict:
    errors: list = []
    warnings: list = []

    _meta(data, errors)
    for entity in SCHEMA:
        if not isinstance(data.get(entity), list):
            errors.append(f"нет списка {entity}")
    if errors:
        return {"errors": errors, "warnings": warnings}

    _fields(data, errors)
    _optional(data, errors)
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
        if (vehicle["condition"] == "ok") != (vehicle["repair_days_left"] is None):
            errors.append(f"vehicles {vehicle['id']}: срок ремонта указывается только "
                          f"у неисправного автобуса")
        ref("vehicles", vehicle, "home_route_id", routes)
    for driver in data["drivers"]:
        ref("drivers", driver, "park_id", parks)
        ref("drivers", driver, "home_vehicle_id", vehicles)
        vehicle = vehicles.get(driver["home_vehicle_id"])
        if vehicle and vehicle["park_id"] != driver["park_id"]:
            errors.append(f"drivers {driver['id']}: водитель парка {driver['park_id']} "
                          f"закреплён за автобусом парка {vehicle['park_id']}")
        if vehicle and vehicle["class"] not in driver["classes"]:
            errors.append(f"drivers {driver['id']}: закреплён за "
                          f"{vehicle['id']}, но нет допуска к классу {vehicle['class']}")
        if driver["schedule"] != "work" and driver["medical"] is not None:
            errors.append(f"drivers {driver['id']}: не работает сегодня, но указан медосмотр")
        if (data.get("meta", {}).get("moment") == "morning" and driver["schedule"] == "work"
                and driver["medical"] is None):
            errors.append(f"drivers {driver['id']}: утро дня, водитель работает, "
                          f"но отметки о медосмотре нет")
    _permits(data, vehicles, routes, errors, warnings)

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
                if route and route["park_id"] != duty["park_id"]:
                    errors.append(f"duties {did}: наряд парка {duty['park_id']}, "
                                  f"а маршрут {route['number']} - парка {route['park_id']}")
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
    if day_type in ("weekday", "weekend") and data["duties"]:
        same = sum(d["day_type"] == day_type for d in data["duties"])
        if not same:
            kinds = sorted({d["day_type"] for d in data["duties"]})
            errors.append(f"в дне нет ни одного наряда на тип дня {day_type}: "
                          f"в нарядах указано {kinds}. Движок построит пустой план")
    for park in data["parks"]:
        pid = park["id"]
        own_vehicles = [v for v in data["vehicles"] if v["park_id"] == pid]
        if len(own_vehicles) != park["list_count"]:
            errors.append(f"парк {pid}: по документам {park['list_count']} автобусов, "
                          f"в списке {len(own_vehicles)}")
        own_duties = [d for d in data["duties"] if d["park_id"] == pid]
        # парк останавливает только down; ЧП (emergency) пока считается рабочим (docs/CONTRACT.md, parks.state)
        if park["state"] == "down" and own_duties:
            warnings.append(f"парк {pid} остановлен, а нарядов у него "
                            f"{len(own_duties)} - расчёт их не закроет")
        elif park["state"] == "emergency":
            warnings.append(f"парк {pid} в режиме ЧП: пока он считается рабочим, "
                            f"наряды закрываются как обычно")
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
                      and d["medical"] != "failed" for d in data["drivers"])
        if working < need_shifts:
            warnings.append(f"парк {pid}: смен {need_shifts}, водителей готово "
                            f"{working}, не хватает {need_shifts - working}")

    return {"errors": errors, "warnings": warnings}
