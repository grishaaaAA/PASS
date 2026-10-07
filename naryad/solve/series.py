"""
Серия дней: автобусы и водители день за днём, и сравнение с ручным способом.

Запуск: python -m naryad.solve.series [--preset park7] [--days 14] [--seed 1] [--labor likely]

Каждый день: сначала автобусы по нарядам (А3), потом водители по сменам
(А4). Ручной способ - две модели (naryad/solve/baseline.py): без проверки
отдыха и со сверкой со вчерашним нарядом. Наш способ помнит прошлые дни
водителей целиком и выравнивает их часы.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from naryad.core.invariants import check_plan, check_rest, use_labor_preset
from naryad.core.model import Day
from naryad.data.generate import generate_series

from .baseline import baseline_drivers
from .drivers import History, solve_drivers
from .vehicles import solve_vehicles


def solve_series(days: list, labor: dict | None = None) -> list:
    """Наш план на каждый день серии: [(день, план)]."""
    history, out = History(), []
    for day in days:
        out.append((day, solve_drivers(day, solve_vehicles(day), history, labor)))
    return out


def manual_series(days: list, check_rest_by_yesterday: bool = True) -> list:
    """Ручной способ на серии дней; False - отдых не проверяется совсем."""
    yesterday = {} if check_rest_by_yesterday else None
    return [(day, baseline_drivers(day, solve_vehicles(day), yesterday)) for day in days]


def series_report(series: list) -> dict:
    needed = filled = own = 0
    minutes = defaultdict(int)
    for day, plan in series:
        problems = check_plan(day, plan)
        if problems:
            raise AssertionError(f"{day.meta['date']}: {problems[0]}")
        for duty_id, vehicle_id in plan.vehicles.items():
            for shift in day.shifts_by_duty.get(duty_id, []):
                needed += 1
                driver_id = plan.drivers.get(shift.id)
                if driver_id is None:
                    continue
                filled += 1
                minutes[driver_id] += shift.length
                own += day.drivers[driver_id].home_vehicle_id == vehicle_id
    rest = check_rest(series)
    hours = sorted(m / 60 for m in minutes.values())
    tenth = statistics.quantiles(hours, n=10)
    return {
        "смен закрыто": f"{filled} из {needed}",
        "нарушений норм отдыха": len(rest),
        "водителей с нарушениями": len({v.ids[0] for v in rest}),
        "на своём закреплённом автобусе, %": round(100 * own / max(1, filled)),
        "часов на водителя: медиана": round(statistics.median(hours)),
        "часов у 80% водителей (без 10% крайних с каждой стороны)": f"{tenth[0]:.0f}-{tenth[-1]:.0f}",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--preset", default="park7")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--start", default="2026-10-05")
    parser.add_argument("--labor", default=None,
                        help="набор норм из naryad/core/labor_presets.json: current, likely, strict")
    args = parser.parse_args(argv)
    use_labor_preset(args.labor)
    days = [Day.from_dict(d) for d in
            generate_series(args.preset, args.start, args.days, args.seed, "morning")]
    columns = {"вручную, отдых не смотрит": series_report(manual_series(days, False)),
               "вручную, сверка со вчера": series_report(manual_series(days)),
               "наш план": series_report(solve_series(days))}
    keys = list(columns["наш план"])
    width = max(map(len, keys))
    print(f"{args.days} дней подряд, {args.preset}, нормы: {args.labor or 'current'}\n")
    print(f"{'':{width}}  " + "  ".join(f"{name:>26}" for name in columns))
    for key in keys:
        print(f"{key:{width}}  " + "  ".join(f"{table[key]!s:>26}" for table in columns.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
