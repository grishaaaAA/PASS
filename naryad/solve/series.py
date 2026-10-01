"""
Серия дней: автобусы и водители день за днём, и сравнение с ручным способом.

Запуск: python -m naryad.solve.series [--preset park7] [--days 14] [--seed 1]

Каждый день: сначала автобусы по нарядам (А3), потом водители по сменам
(А4). Наш способ помнит прошлые дни водителей, ручной - нет.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from naryad.core.invariants import check_plan, check_rest
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


def manual_series(days: list) -> list:
    return [(day, baseline_drivers(day, solve_vehicles(day))) for day in days]


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
    return {
        "смен закрыто": f"{filled} из {needed}",
        "нарушений норм отдыха": len(rest),
        "водителей с нарушениями": len({v.ids[0] for v in rest}),
        "на своём закреплённом автобусе, %": round(100 * own / max(1, filled)),
        "часов на водителя: меньше всех": round(hours[0]),
        "часов на водителя: медиана": round(statistics.median(hours)),
        "часов на водителя: больше всех": round(hours[-1]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--preset", default="park7")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--start", default="2026-10-05")
    args = parser.parse_args(argv)
    days = [Day.from_dict(d) for d in
            generate_series(args.preset, args.start, args.days, args.seed, "morning")]
    manual, ours = series_report(manual_series(days)), series_report(solve_series(days))
    width = max(map(len, manual))
    print(f"{args.days} дней подряд, {args.preset}\n")
    print(f"{'':{width}}  {'вручную':>12}  {'наш план':>12}")
    for key in manual:
        print(f"{key:{width}}  {manual[key]!s:>12}  {ours[key]!s:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
