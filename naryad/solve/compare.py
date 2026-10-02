"""
Сравнение: ручная расстановка против нашей, на днях с нехваткой автобусов.

Запуск: python -m naryad.solve.compare [файл дня] [--seeds N]

Сценарий нехватки: утром не вышла заданная доля исправных автобусов
каждого парка (не завелись в мороз, сошли на ТО). Какие именно, а значит
и каких классов, - случайно по номеру набора; одинаковый номер даёт
одинаковый сценарий у обоих способов. Показываем среднее по наборам и
худший набор.

Ручной способ - аккуратная модель из naryad/solve/baseline.py: линия
раньше резерва, свои автобусы на свои маршруты.
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
SCENARIOS = {"нехватка 5%": 0.05, "нехватка 10%": 0.10, "нехватка 15%": 0.15, "нехватка 20%": 0.20}
MORE_IS_BETTER = {"закрыто нарядов на линии", "свои автобусы на своих маршрутах, %"}


def with_shortage(day: Day, share: float, seed: int, by_class: bool = True) -> Day:
    """Копия дня, где доля исправных автобусов не вышла.

    by_class=True - ровно доля share в каждом классе каждого парка (один и
    тот же сценарий при любом seed, меняется только, какие борта выбыли).
    by_class=False - ровно доля share в каждом парке, классы - как выпадет.
    """
    rng = random.Random(seed)
    out = dataclasses.replace(day, vehicles=dict(day.vehicles))
    groups = defaultdict(list)
    for v in day.vehicles.values():
        if v.condition == "ok":
            groups[(v.park_id, v.cls) if by_class else v.park_id].append(v)
    for group in groups.values():
        for v in rng.sample(sorted(group, key=lambda v: v.id), round(len(group) * share)):
            out.vehicles[v.id] = dataclasses.replace(v, condition="repair")
    return out


def worst_growth(plan: Plan, duties: list) -> float:
    """Во сколько раз вырос интервал на маршруте в худший момент дня.

    В каждый момент: нарядов маршрута по плану / сколько из них вышло, как
    в explain.interval. inf - был момент, когда на маршруте не было ни
    одного автобуса.
    """
    worst = 1.0
    for t in {d.start for d in duties} | {d.end for d in duties}:
        active = [d for d in duties if d.start <= t < d.end]
        running = sum(d.id in plan.vehicles for d in active)
        if active:
            worst = max(worst, len(active) / running if running else float("inf"))
    return worst


def report(day: Day, plan: Plan) -> dict:
    """Что видит диспетчер и пассажир: сколько закрыто и как выросли интервалы."""
    lines = [d for d in day.duties.values() if d.day_type == day.day_type and d.type == "line"]
    by_route = defaultdict(list)
    for d in lines:
        by_route[d.route_id].append(d)
    lost = [d for d in lines if d.id not in plan.vehicles]
    growth = [worst_growth(plan, duties) for duties in by_route.values()]
    own = [d for d in lines if d.id in plan.vehicles
           and day.vehicles[plan.vehicles[d.id]].home_route_id == d.route_id]
    return {
        "закрыто нарядов на линии": len(lines) - len(lost),
        "потеряно часов на линии": round(sum(d.end - d.start for d in lost) / 60),
        "потеряно нарядов важных маршрутов": sum(day.routes[d.route_id].priority == 1 for d in lost),
        "худший рост интервала, раз": round(max(g for g in growth if g != float("inf")), 2),
        "маршрутов с ростом интервала > 25%": sum(g > 1.25 for g in growth),
        "маршрутов, где был момент без автобусов": sum(g == float("inf") for g in growth),
        "свои автобусы на своих маршрутах, %": round(100 * len(own) / max(1, len(lines) - len(lost))),
        "цена пропусков": round(total_cost(day, plan), 1),
    }


def compare(day: Day, seeds: int = 10) -> dict:
    """{сценарий: {способ: {показатель: (среднее, худший набор)}}}."""
    result = {}
    for name, share in SCENARIOS.items():
        rows = {"вручную": [], "наш план": []}
        for seed in range(1, seeds + 1):
            scenario = with_shortage(day, share, seed, by_class=False)
            for label, solver in (("вручную", baseline_vehicles), ("наш план", solve_vehicles)):
                plan = solver(scenario)
                problems = check_plan(scenario, plan, drivers=False)
                if problems:
                    raise AssertionError(f"{label}: план недопустим: {problems[0]}")
                rows[label].append(report(scenario, plan))
        result[name] = {label: _summary(items) for label, items in rows.items()}
    return result


def _summary(items: list) -> dict:
    out = {}
    for key in items[0]:
        values = [i[key] for i in items]
        worst = min(values) if key in MORE_IS_BETTER else max(values)
        out[key] = (round(statistics.mean(values), 2), worst)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("day", nargs="?", default=str(DEFAULT_DAY))
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args(argv)
    day = Day.load(args.day)
    lines = sum(d.day_type == day.day_type and d.type == "line" for d in day.duties.values())
    print(f"Нарядов на линии: {lines}. В клетке: среднее по {args.seeds} сценариям (худший сценарий).")
    for name, table in compare(day, args.seeds).items():
        print(f"\n{name}")
        keys = list(table["вручную"])
        width = max(map(len, keys))
        print(f"{'':{width}}  {'вручную':>16}  {'наш план':>16}")
        for key in keys:
            cells = [f"{m:g}" if m == w else f"{m:g} ({w:g})" for m, w in
                     (table["вручную"][key], table["наш план"][key])]
            print(f"{key:{width}}  {cells[0]:>16}  {cells[1]:>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
