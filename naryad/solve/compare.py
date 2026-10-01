"""
Сравнение: ручная расстановка против нашей, на днях с нехваткой автобусов.

Запуск: python -m naryad.solve.compare [файл дня] [--seeds N]

Сценарии нехватки: часть исправных автобусов утром не выходит (не
завелись в мороз, сошли на ТО). Какие именно - случайно, по номеру
набора: одинаковый номер даёт одинаковый сценарий у обоих способов.
"""

from __future__ import annotations

import argparse
import dataclasses
import random
import statistics
from collections import defaultdict
from pathlib import Path

from naryad.core.invariants import check_plan
from naryad.core.model import Day, Plan

from .baseline import baseline_vehicles
from .vehicles import solve_vehicles, total_cost

DEFAULT_DAY = Path(__file__).resolve().parents[2] / "data" / "samples" / "park7_weekday.json"
SCENARIOS = {"нехватка 5%": 0.05, "нехватка 10%": 0.10, "нехватка 15%": 0.15}


def with_shortage(day: Day, share: float, seed: int) -> Day:
    """Копия дня, где доля исправных автобусов каждого класса не вышла."""
    rng = random.Random(seed)
    out = dataclasses.replace(day, vehicles=dict(day.vehicles))
    by_class = defaultdict(list)
    for v in day.vehicles.values():
        if v.condition == "ok":
            by_class[(v.park_id, v.cls)].append(v)
    for group in by_class.values():
        for v in rng.sample(sorted(group, key=lambda v: v.id), round(len(group) * share)):
            out.vehicles[v.id] = dataclasses.replace(v, condition="repair")
    return out


def report(day: Day, plan: Plan) -> dict:
    """Что видит диспетчер и пассажир: сколько закрыто и как выросли интервалы."""
    lines = [d for d in day.duties.values() if d.day_type == day.day_type and d.type == "line"]
    by_route = defaultdict(list)
    for d in lines:
        by_route[d.route_id].append(d)
    lost = [d for d in lines if d.id not in plan.vehicles]
    growth = []
    for route_id, duties in by_route.items():
        n, m = len(duties), sum(d.id not in plan.vehicles for d in duties)
        growth.append(float("inf") if m == n else n / (n - m))
    own = [d for d in lines if d.id in plan.vehicles
           and day.vehicles[plan.vehicles[d.id]].home_route_id == d.route_id]
    return {
        "закрыто на линии": f"{len(lines) - len(lost)} из {len(lines)}",
        "потеряно часов на линии": round(sum(d.end - d.start for d in lost) / 60),
        "потеряно нарядов важных маршрутов": sum(day.routes[d.route_id].priority == 1 for d in lost),
        "худший рост интервала, раз": round(max(growth), 2),
        "маршрутов с ростом интервала > 25%": sum(g > 1.25 for g in growth),
        "свои автобусы на своих маршрутах, %": round(100 * len(own) / max(1, len(lines) - len(lost))),
        "цена пропусков": round(total_cost(day, plan), 1),
    }


def compare(day: Day, seeds: int = 5) -> dict:
    """Среднее по сценариям и наборам: {сценарий: {способ: {показатель: значение}}}."""
    result = {}
    for name, share in SCENARIOS.items():
        rows = {"вручную": [], "наш план": []}
        for seed in range(1, seeds + 1):
            scenario = with_shortage(day, share, seed)
            for label, solver in (("вручную", baseline_vehicles), ("наш план", solve_vehicles)):
                plan = solver(scenario)
                problems = check_plan(scenario, plan, drivers=False)
                if problems:
                    raise AssertionError(f"{label}: план недопустим: {problems[0]}")
                rows[label].append(report(scenario, plan))
        result[name] = {label: _average(items) for label, items in rows.items()}
    return result


def _average(items: list) -> dict:
    out = {}
    for key, value in items[0].items():
        if isinstance(value, str):
            out[key] = value if len({i[key] for i in items}) == 1 else f"{items[0][key]} ..."
        else:
            out[key] = round(statistics.mean(i[key] for i in items), 2)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("day", nargs="?", default=str(DEFAULT_DAY))
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    day = Day.load(args.day)
    for name, table in compare(day, args.seeds).items():
        print(f"\n{name} (среднее по {args.seeds} наборам)")
        keys = list(table["вручную"])
        width = max(map(len, keys))
        print(f"{'':{width}}  {'вручную':>12}  {'наш план':>12}")
        for key in keys:
            print(f"{key:{width}}  {table['вручную'][key]!s:>12}  {table['наш план'][key]!s:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
