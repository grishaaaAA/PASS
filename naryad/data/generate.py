"""
Генератор данных: город по вводным + состояние на каждый день.

Постоянная часть (парки, маршруты, наряды, автобусы, водители) строится
один раз по вводным и номеру набора. Меняющаяся часть (исправность,
выход водителей, медосмотр) считается день за днём.

Запуск:
    один день:      python -m naryad.data.generate --date 2026-10-05 --out day.json
    несколько дней: python -m naryad.data.generate --date 2026-10-05 --days 30 --out series/
    свои вводные:   ... --parks 3 --release 250 --routes 50 --mix 700,700,500
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date as Date, timedelta
from pathlib import Path

from .check import check
from .city import build_city, resolve
from .days import advance, day_type_of, initial_state, snapshot
from .presets import ASSUMPTIONS, CASE_ASSUMPTIONS, CLASS_LABELS, CLASSES

FORMAT_VERSION = "0.2"
STATUS_LABELS = {"work": "работает", "day_off": "выходной", "sick": "больничный",
                 "vacation": "отпуск"}


def _meta(config: dict, seed: int, day: Date, index: int, moment: str) -> dict:
    inputs = {k: v for k, v in config.items() if k not in ("kind", "parks", "source", "preset")}
    assumptions = list(ASSUMPTIONS)
    if config["kind"] == "synthetic":
        assumptions += CASE_ASSUMPTIONS
    return {
        "format_version": FORMAT_VERSION,
        "generator": "naryad.data.generate",
        "preset": config["preset"],
        "inputs": json.loads(json.dumps(inputs)),
        "seed": seed,
        "date": day.isoformat(),
        "day_type": day_type_of(day),
        "day_index": index,
        "moment": moment,
        "source": config["source"],
        "assumptions": assumptions,
    }


def generate_series(preset: str = "case", start: str = "2026-10-05", days: int = 1,
                    seed: int = 1, moment: str = "morning", **inputs):
    """Набор на каждый день подряд, начиная со start. Отдаёт по одному дню."""
    if moment not in ("plan", "morning"):
        raise ValueError("moment: 'plan' или 'morning'")
    if days < 1:
        raise ValueError("дней должно быть не меньше 1")
    config = resolve(preset, **inputs)
    city = build_city(config, seed)
    first = Date.fromisoformat(start)
    state = initial_state(city, seed)
    for index in range(days):
        day = first + timedelta(days=index)
        if index:
            state = advance(city, state, day, seed)
        data = {"meta": _meta(config, seed, day, index, moment)}
        data.update(snapshot(city, state, day, seed, moment))
        yield data


def generate(preset: str = "case", day: str = "2026-10-05", seed: int = 1,
             moment: str = "morning", **inputs) -> dict:
    """Один день. Это первый день серии, неисправных - ровно по тех. готовности."""
    return next(generate_series(preset, day, 1, seed, moment, **inputs))


def day_numbers(data: dict, park_id: str | None = None) -> dict:
    """Главные числа дня по парку или по всему городу."""
    def own(items):
        return [x for x in items if park_id is None or x["park_id"] == park_id]

    vehicles, duties, drivers = own(data["vehicles"]), own(data["duties"]), own(data["drivers"])
    duty_ids = {d["id"] for d in duties}
    ok = [v for v in vehicles if v["condition"] == "ok"]
    line = sum(d["type"] == "line" for d in duties)
    numbers = {
        "vehicles": len(vehicles),
        "ok": len(ok),
        "repair": sum(v["condition"] == "repair" for v in vehicles),
        "maintenance": sum(v["condition"] == "maintenance" for v in vehicles),
        "line": line,
        "reserve": len(duties) - line,
        "idle": len(ok) - len(duties),
        "drivers": len(drivers),
        "shifts": sum(s["duty_id"] in duty_ids for s in data["shifts"]),
        "medical_failed": sum(d["medical"] == "failed" for d in drivers),
    }
    for status in STATUS_LABELS:
        numbers[status] = sum(d["schedule"] == status for d in drivers)
    numbers["ready"] = numbers["work"] - numbers["medical_failed"]
    for cls in CLASSES:
        numbers[f"ok_{cls}"] = sum(v["class"] == cls for v in ok)
        numbers[f"need_{cls}"] = sum(d["vehicle_class"] == cls for d in duties)
    return numbers


def summary(data: dict) -> str:
    meta = data["meta"]
    label = "будни" if meta["day_type"] == "weekday" else "выходной"
    n = day_numbers(data)
    lines = [
        f"{meta['date']}, {label}: парков {len(data['parks'])}, маршрутов "
        f"{len(data['routes'])}, номер набора {meta['seed']}",
        f"  Автобусы  по списку {n['vehicles']}, исправны {n['ok']}, ремонт {n['repair']}, "
        f"ТО {n['maintenance']}",
        f"  Наряды    на маршрутах {n['line']}, резерв {n['reserve']}, "
        f"исправны и не нужны {n['idle']}",
        f"  Водители  по списку {n['drivers']}, работают {n['work']}, выходной "
        f"{n['day_off']}, больничный {n['sick']}, отпуск {n['vacation']}, "
        f"не прошли медосмотр {n['medical_failed']}",
        f"  Смен закрыть {n['shifts']}, водителей готово {n['ready']}",
        "  По классам: исправны / нужно (маршруты + резерв)",
    ]
    for cls in CLASSES:
        if n[f"ok_{cls}"] or n[f"need_{cls}"]:
            lines.append(f"    {CLASS_LABELS[cls]:<15} {n[f'ok_{cls}']:>5} / {n[f'need_{cls}']}")
    lines.append("  По паркам: исправны / нужно, водителей готово / смен")
    for park in data["parks"]:
        p = day_numbers(data, park["id"])
        lines.append(f"    {park['name']:<28} {p['ok']:>4} / {p['line'] + p['reserve']:<4}"
                     f" {p['ready']:>5} / {p['shifts']}")
    return "\n".join(lines)


SERIES_COLUMNS = [
    ("date", "дата"), ("day_type", "тип дня"), ("ok", "исправны"), ("repair", "ремонт"),
    ("maintenance", "ТО"), ("line", "на маршруты"), ("reserve", "резерв"),
    ("idle", "исправны и не нужны"), ("work", "водители работают"),
    ("sick", "больничный"), ("vacation", "отпуск"), ("medical_failed", "не прошли медосмотр"),
    ("ready", "водителей готово"), ("shifts", "смен закрыть"),
    ("errors", "ошибок"), ("warnings", "предупреждений"),
]


def write_series(series, folder) -> list:
    """Каждый день - отдельный файл days/ГГГГ-ММ-ДД.json, плюс сводка series.csv."""
    folder = Path(folder)
    (folder / "days").mkdir(parents=True, exist_ok=True)
    rows = []
    for data in series:
        report = check(data)
        path = folder / "days" / f"{data['meta']['date']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        row = day_numbers(data)
        row.update(date=data["meta"]["date"], day_type=data["meta"]["day_type"],
                   errors=len(report["errors"]), warnings=len(report["warnings"]))
        rows.append(row)
    with open(folder / "series.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([title for _, title in SERIES_COLUMNS])
        for row in rows:
            writer.writerow([row[key] for key, _ in SERIES_COLUMNS])
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Генератор данных для нарядки")
    parser.add_argument("--preset", default="case", choices=("case", "park7"),
                        help="case - вводные кейса, park7 - реальный парк №7")
    parser.add_argument("--date", default="2026-10-05", help="первый день, ГГГГ-ММ-ДД")
    parser.add_argument("--days", type=int, default=1, help="сколько дней подряд")
    parser.add_argument("--seed", type=int, default=1, help="номер набора")
    parser.add_argument("--moment", default="morning", choices=("plan", "morning"))
    parser.add_argument("--parks", type=int, help="сколько парков (1-7)")
    parser.add_argument("--release", type=int, help="выпуск одного парка в день")
    parser.add_argument("--routes", type=int, help="маршрутов всего")
    parser.add_argument("--mix", help="классы средний,большой,особо большой, напр. 700,700,500")
    parser.add_argument("--readiness", type=float, help="тех. готовность, напр. 0.893")
    parser.add_argument("--weekend", type=float, help="выпуск в выходной от будней, напр. 0.76")
    parser.add_argument("--out", help="файл .json для одного дня или папка для серии")
    parser.add_argument("--csv", help="папка для выгрузки первого дня в CSV")
    args = parser.parse_args(argv)

    inputs = {
        "park_count": args.parks,
        "release_per_park": args.release,
        "routes_total": args.routes,
        "tech_readiness": args.readiness,
        "weekend_factor": args.weekend,
    }
    if args.mix:
        medium, big, extra = (int(x) for x in args.mix.split(","))
        inputs["class_mix"] = {"medium": medium, "big": big, "extra_big": extra}
    if args.preset == "park7" and any(v is not None for v in inputs.values()):
        parser.error("у park7 вводные берутся из справки, менять их нельзя")
    inputs = {k: v for k, v in inputs.items() if v is not None}

    series = generate_series(args.preset, args.date, args.days, args.seed, args.moment, **inputs)
    if args.days == 1:
        data = next(series)
        print(summary(data))
        report = check(data)
        for line in report["warnings"]:
            print(f"  ! {line}")
        for line in report["errors"]:
            print(f"  ОШИБКА: {line}")
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

    if not args.out:
        parser.error("для нескольких дней укажи --out папку")
    rows = write_series(series, args.out)
    print(f"{'дата':<11}{'исправны':>9}{'нужно':>7}{'вод. готово':>12}{'смен':>6}"
          f"{'ошибок':>8}{'предупр.':>9}")
    for row in rows:
        print(f"{row['date']:<11}{row['ok']:>9}{row['line'] + row['reserve']:>7}"
              f"{row['ready']:>12}{row['shifts']:>6}{row['errors']:>8}{row['warnings']:>9}")
    print(f"Папка: {args.out} (days/*.json, series.csv)")
    return 1 if any(row["errors"] for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
