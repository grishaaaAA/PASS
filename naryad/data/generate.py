"""
Генератор синтетических данных парка.

Один и тот же набор параметров (настройка, дата, номер набора, момент)
всегда даёт одни и те же данные: иначе нельзя сравнить работу движка
до и после правок.

Генератор задаёт только исходное состояние: какие автобусы исправны,
какие наряды нужно закрыть, какие водители работают. На какой наряд
встанет какой автобус и водитель - решает движок, не генератор.

Запуск:
    python -m naryad.data.generate --date 2026-10-05 --seed 1 \\
        --out data/samples/park7_weekday.json --csv data/samples/park7_weekday_csv
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date as Date
from pathlib import Path

from .check import check
from .names import full_name
from .presets import ASSUMPTIONS, CLASS_LABELS, CLASSES, PRESETS

FORMAT_VERSION = "0.1"

PLATE_LETTERS = "АВЕКМНОРСТУХ"
PLATE_REGIONS = ("78", "98", "178", "198")

# Доля ремонта среди неисправных, остальное - ТО.
REPAIR_SHARE = 0.6
# Сколько особо больших маршрутов набираем от ожидаемо исправных сочленённых.
BIG_CLASS_FILL = 0.85
SPEED_KMH = 18
LAYOVER_MIN = 20
DRIVERS_PER_SHIFT = 1.4
WORKING_SPARE = 1.05
EXTRA_PERMIT_BOOST = 1.15
DRIVERS_PER_VEHICLE = 2

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


def day_type_of(day: Date) -> str:
    return "weekend" if day.weekday() >= 5 else "weekday"


def _fleet_by_class(preset: dict) -> dict:
    counts: dict = {}
    for _, cls, _, count in preset["fleet"]:
        counts[cls] = counts.get(cls, 0) + count
    return counts


def _park(preset: dict) -> dict:
    return {
        "id": preset["id"],
        "name": preset["name"],
        "address": preset["address"],
        "lat": preset["lat"],
        "lon": preset["lon"],
        "state": "working",
        "list_count": preset["list_count"],
        "tech_readiness": preset["tech_readiness"],
        "release_weekday": preset["release"]["weekday"],
        "release_weekend": preset["release"]["weekend"],
        "reserve_weekday": preset["reserve"]["weekday"],
        "reserve_weekend": preset["reserve"]["weekend"],
    }


def _route_classes(preset: dict) -> dict:
    """Самым загруженным маршрутам - самый вместительный класс, пока хватает машин."""
    rows = preset["routes"]
    fleet = _fleet_by_class(preset)
    present = [cls for cls in reversed(CLASSES) if fleet.get(cls)]
    order = sorted(range(len(rows)), key=lambda i: -rows[i][1])
    class_of: dict = {}
    for cls in present[:-1]:
        cap = BIG_CLASS_FILL * fleet[cls] * preset["tech_readiness"]
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
    return class_of


def _routes(preset: dict, rng: random.Random) -> list:
    rows = preset["routes"]
    class_of = _route_classes(preset)
    busy = sorted((i for i in range(len(rows)) if rows[i][1] > 0),
                  key=lambda i: -rows[i][1])
    third = len(busy) / 3
    priority = {i: 1 if rank < third else 2 if rank < 2 * third else 3
                for rank, i in enumerate(busy)}
    routes = []
    for i, (number, weekday, weekend, length) in enumerate(rows):
        source = "справка"
        if length is None:
            length = round(rng.uniform(10, 25), 2)
            source = "синтетика"
        turnaround = 2 * length / SPEED_KMH * 60 + LAYOVER_MIN
        routes.append({
            "id": f"{preset['id']}-R{i + 1:02d}",
            "park_id": preset["id"],
            "number": number,
            "allowed_classes": [class_of[i]],
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


def _vehicles(preset: dict, routes: list, rng: random.Random) -> list:
    park_number = int("".join(ch for ch in preset["id"] if ch.isdigit()))
    used_plates: set = set()
    vehicles = []
    for model, cls, fuel, count in preset["fleet"]:
        for _ in range(count):
            n = len(vehicles) + 1
            vehicles.append({
                "id": f"{preset['id']}-V{n:04d}",
                "board_number": str(park_number * 1000 + n),
                "plate": _plate(rng, used_plates),
                "model": model,
                "class": cls,
                "fuel": fuel,
                "park_id": preset["id"],
                "condition": "ok",
                "home_route_id": None,
            })

    by_class: dict = {}
    for vehicle in vehicles:
        by_class.setdefault(vehicle["class"], []).append(vehicle)

    broken_total = round(len(vehicles) * (1 - preset["tech_readiness"]))
    broken = split_total(broken_total, {c: len(v) for c, v in by_class.items()})
    for cls, group in by_class.items():
        chosen = rng.sample(group, broken[cls])
        repair = round(len(chosen) * REPAIR_SHARE)
        for k, vehicle in enumerate(chosen):
            vehicle["condition"] = "repair" if k < repair else "maintenance"

    # Свой маршрут: каждому маршруту столько машин его класса, сколько у него
    # нарядов в будни. Оставшиеся - без маршрута.
    for cls, group in by_class.items():
        pool = group[:]
        rng.shuffle(pool)
        own = sorted((r for r in routes if r["allowed_classes"][0] == cls),
                     key=lambda r: -r["duties_weekday"])
        for route in own:
            for _ in range(route["duties_weekday"]):
                if not pool:
                    break
                pool.pop()["home_route_id"] = route["id"]
    return vehicles


def _duties(preset: dict, routes: list, vehicles: list, day_type: str,
            rng: random.Random) -> tuple:
    count_key = f"duties_{day_type}"
    plan = []  # (id, тип, маршрут, класс)
    for route in routes:
        for k in range(route[count_key]):
            plan.append((f"{route['id']}-{k + 1:02d}", "line", route["id"],
                         route["allowed_classes"][0]))

    healthy: dict = {}
    for vehicle in vehicles:
        if vehicle["condition"] == "ok":
            healthy[vehicle["class"]] = healthy.get(vehicle["class"], 0) + 1
    need: dict = {}
    for _, _, _, cls in plan:
        need[cls] = need.get(cls, 0) + 1
    spare = {cls: max(0, healthy[cls] - need.get(cls, 0)) for cls in healthy}
    if not any(spare.values()):
        spare = dict(healthy)
    reserve_total = preset["reserve"][day_type]
    reserve_by_class = split_total(reserve_total, spare)
    n = 0
    for cls in CLASSES:
        for _ in range(reserve_by_class.get(cls, 0)):
            n += 1
            plan.append((f"{preset['id']}-RES-{n:02d}", "reserve", None, cls))

    mix = preset["shift_mix"][day_type]
    shift_counts = [k for k in (1, 2, 3) for _ in range(mix[k])]
    if len(shift_counts) != len(plan):
        raise ValueError(
            f"Смен по видам ({len(shift_counts)}) не равно нарядам на маршрутах "
            f"и в резерве ({len(plan)}): проверь настройку парка {preset['id']}")
    rng.shuffle(shift_counts)

    duties, shifts = [], []
    for (duty_id, kind, route_id, cls), k in zip(plan, shift_counts):
        (start_lo, start_hi), (dur_lo, dur_hi) = DUTY_TIMES[k]
        start = _step(rng, start_lo, start_hi)
        end = start + _step(rng, dur_lo, dur_hi)
        duties.append({
            "id": duty_id,
            "park_id": preset["id"],
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


def _medical(rng: random.Random, working: bool, moment: str):
    if not working:
        return None
    if moment == "plan":
        return "pending"
    roll = rng.random()
    return "passed" if roll < 0.97 else "failed" if roll < 0.98 else "pending"


def _drivers(preset: dict, vehicles: list, shifts_today: int, day_type: str,
             moment: str, rng: random.Random) -> list:
    weekday_shifts = sum(k * n for k, n in preset["shift_mix"]["weekday"].items())
    total = round(weekday_shifts * DRIVERS_PER_SHIFT)
    working = set(rng.sample(range(total), min(total, round(shifts_today * WORKING_SPARE))))

    fleet = _fleet_by_class(preset)
    top_class = [c for c in CLASSES if fleet.get(c)]
    extra_share = 0.0
    if "extra_big" in fleet:
        extra_share = min(1.0, fleet["extra_big"] / sum(fleet.values()) * EXTRA_PERMIT_BOOST)
    extra = set(rng.sample(range(total), round(total * extra_share)))
    base_top = "big" if fleet.get("big") else top_class[0]

    park_number = int("".join(ch for ch in preset["id"] if ch.isdigit()))
    drivers = []
    for n in range(total):
        top = "extra_big" if n in extra else base_top
        drivers.append({
            "id": f"{preset['id']}-D{n + 1:04d}",
            "tab_number": f"{park_number:02d}{n + 1:04d}",
            "full_name": full_name(rng),
            "park_id": preset["id"],
            "classes": list(CLASSES[:CLASSES.index(top) + 1]),
            "home_vehicle_id": None,
            "schedule": "work" if n in working else "day_off",
            "medical": _medical(rng, n in working, moment),
        })

    # Закрепление: сначала водители ровно с этим допуском, потом с бо́льшим.
    pools: dict = {}
    for driver in drivers:
        pools.setdefault(driver["classes"][-1], []).append(driver)
    for pool in pools.values():
        rng.shuffle(pool)
    attached = [v for v in vehicles if v["home_route_id"]]
    rng.shuffle(attached)
    for vehicle in attached:
        for _ in range(DRIVERS_PER_VEHICLE):
            for cls in CLASSES[CLASSES.index(vehicle["class"]):]:
                if pools.get(cls):
                    pools[cls].pop()["home_vehicle_id"] = vehicle["id"]
                    break
    return drivers


def generate(preset: str = "park7", day: str = "2026-10-05", seed: int = 1,
             moment: str = "plan") -> dict:
    """
    preset - имя готовой настройки парка;
    day    - дата в виде ГГГГ-ММ-ДД, по ней выбираются будни или выходные;
    seed   - номер набора: одинаковый номер даёт одинаковые данные;
    moment - 'plan' (план на завтра, медосмотр ещё не проходили)
             или 'morning' (утро дня, медосмотр уже идёт).
    """
    if preset not in PRESETS:
        raise ValueError(f"Нет настройки «{preset}», есть: {', '.join(PRESETS)}")
    if moment not in ("plan", "morning"):
        raise ValueError("moment: 'plan' или 'morning'")
    config = PRESETS[preset]
    day_type = day_type_of(Date.fromisoformat(day))
    rng = random.Random(seed)

    routes = _routes(config, rng)
    vehicles = _vehicles(config, routes, rng)
    duties, shifts = _duties(config, routes, vehicles, day_type, rng)
    drivers = _drivers(config, vehicles, len(shifts), day_type, moment, rng)
    return {
        "meta": {
            "format_version": FORMAT_VERSION,
            "generator": "naryad.data.generate",
            "preset": preset,
            "seed": seed,
            "date": day,
            "day_type": day_type,
            "moment": moment,
            "source": config["source"],
            "assumptions": ASSUMPTIONS,
        },
        "parks": [_park(config)],
        "routes": routes,
        "duties": duties,
        "shifts": shifts,
        "vehicles": vehicles,
        "drivers": drivers,
    }


def summary(data: dict) -> str:
    """Короткая сводка по каждому парку: от списка автобусов до нарядов."""
    lines = []
    day_type = data["meta"]["day_type"]
    for park in data["parks"]:
        pid = park["id"]
        vehicles = [v for v in data["vehicles"] if v["park_id"] == pid]
        duties = [d for d in data["duties"] if d["park_id"] == pid]
        drivers = [d for d in data["drivers"] if d["park_id"] == pid]
        duty_ids = {d["id"] for d in duties}
        shifts = [s for s in data["shifts"] if s["duty_id"] in duty_ids]
        ok = [v for v in vehicles if v["condition"] == "ok"]
        line = [d for d in duties if d["type"] == "line"]
        reserve = [d for d in duties if d["type"] == "reserve"]
        repair = sum(v["condition"] == "repair" for v in vehicles)
        upkeep = sum(v["condition"] == "maintenance" for v in vehicles)
        working = [d for d in drivers if d["schedule"] == "work"]
        label = "будни" if day_type == "weekday" else "выходной"
        lines += [
            f"{park['name']} ({pid}), {data['meta']['date']}, {label}",
            f"  Автобусов по списку        {len(vehicles):>5}  (по документам {park['list_count']})",
            f"  Исправны                   {len(ok):>5}",
            f"  Неисправны                 {len(vehicles) - len(ok):>5}  (ремонт {repair}, ТО {upkeep})",
            f"  Нужно на маршруты          {len(line):>5}",
            f"  Нужно в резерв             {len(reserve):>5}",
            f"  Исправны и не нужны        {len(ok) - len(line) - len(reserve):>5}",
            "  По классам (исправны / нужно на маршруты + резерв):",
        ]
        for cls in CLASSES:
            have = sum(v["class"] == cls for v in ok)
            need = sum(d["vehicle_class"] == cls for d in duties)
            if have or need:
                lines.append(f"    {CLASS_LABELS[cls]:<15} {have:>5} / {need}")
        lines += [
            f"  Водителей по списку        {len(drivers):>5}",
            f"  Работают по графику        {len(working):>5}",
            f"  Смен нужно закрыть         {len(shifts):>5}",
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Генератор данных парка")
    parser.add_argument("--preset", default="park7")
    parser.add_argument("--date", default="2026-10-05", help="ГГГГ-ММ-ДД")
    parser.add_argument("--seed", type=int, default=1, help="номер набора")
    parser.add_argument("--moment", default="plan", choices=("plan", "morning"))
    parser.add_argument("--out", help="куда сохранить JSON")
    parser.add_argument("--csv", help="папка для выгрузки в CSV")
    args = parser.parse_args(argv)

    data = generate(args.preset, args.date, args.seed, args.moment)
    print(summary(data))
    report = check(data)
    for warning in report["warnings"]:
        print(f"  ! {warning}")
    for error in report["errors"]:
        print(f"  ОШИБКА: {error}")

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON: {path}")
    if args.csv:
        from .csvio import write_csv
        write_csv(data, args.csv)
        print(f"CSV:  {args.csv}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
