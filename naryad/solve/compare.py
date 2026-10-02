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

Второе сравнение - нехватка водителей: часть водителей, работающих по
графику, заболела. Показываем, на каких сменах остаются пустые места.
"""

from __future__ import annotations

import argparse
import dataclasses
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from naryad.core.invariants import check_plan, use_labor_preset
from naryad.core.model import Day, Plan

from .baseline import baseline_drivers, baseline_vehicles
from .drivers import solve_drivers
from .vehicles import solve_vehicles, total_cost

DEFAULT_DAY = Path(__file__).resolve().parents[2] / "data" / "samples" / "park7_weekday.json"
SCENARIOS = {"нехватка 5%": 0.05, "нехватка 10%": 0.10, "нехватка 15%": 0.15, "нехватка 20%": 0.20}
MORE_IS_BETTER = {"закрыто нарядов на линии", "свои автобусы на своих маршрутах, %"}
DRIVER_SCENARIOS = {"болеют 10% водителей": 0.10, "болеют 20% водителей": 0.20, "болеют 30% водителей": 0.30}


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


def with_sick_drivers(day: Day, share: float, seed: int) -> Day:
    """Копия дня, где доля водителей, работающих по графику, заболела."""
    rng = random.Random(seed)
    out = dataclasses.replace(day, drivers=dict(day.drivers))
    working = sorted(d.id for d in day.drivers.values() if d.schedule == "work")
    for driver_id in rng.sample(working, round(len(working) * share)):
        out.drivers[driver_id] = dataclasses.replace(out.drivers[driver_id], schedule="sick")
    return out


def unfilled_shifts(day: Day, plan: Plan) -> Counter:
    """Пустые смены на выпущенных нарядах: по важности маршрута и резерв."""
    out = Counter()
    for duty_id in plan.vehicles:
        duty = day.duties[duty_id]
        key = "резерв" if duty.type == "reserve" else f"важность {day.routes[duty.route_id].priority}"
        out[key] += sum(s.id not in plan.drivers for s in day.shifts_by_duty.get(duty_id, []))
    return out


def compare_drivers(day: Day, seeds: int = 5, scenarios: dict = DRIVER_SCENARIOS) -> dict:
    """{сценарий: {способ: {вид смены: пустых смен в день, среднее}}}."""
    result = {}
    for name, share in scenarios.items():
        rows = {"вручную": Counter(), "наш план": Counter()}
        for seed in range(1, seeds + 1):
            scenario = with_sick_drivers(day, share, seed)
            vehicles = solve_vehicles(scenario)
            for label, plan in (("вручную", baseline_drivers(scenario, vehicles, {})),
                                ("наш план", solve_drivers(scenario, vehicles))):
                problems = check_plan(scenario, plan)
                if problems:
                    raise AssertionError(f"{label}: план недопустим: {problems[0]}")
                rows[label] += unfilled_shifts(scenario, plan)
        kinds = sorted(set(rows["вручную"]) | set(rows["наш план"]))
        result[name] = {label: {k: round(c[k] / seeds, 1) for k in kinds} for label, c in rows.items()}
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
    parser.add_argument("--labor", default=None,
                        help="набор норм из naryad/core/labor_presets.json: current, likely, strict")
    args = parser.parse_args(argv)
    use_labor_preset(args.labor)
    day = Day.load(args.day)
    lines = sum(d.day_type == day.day_type and d.type == "line" for d in day.duties.values())
    print(f"Нарядов на линии: {lines}. Нормы: {args.labor or 'current'}. "
          f"В клетке: среднее по {args.seeds} сценариям (худший сценарий).")
    for name, table in compare(day, args.seeds).items():
        print(f"\n{name}")
        keys = list(table["вручную"])
        width = max(map(len, keys))
        print(f"{'':{width}}  {'вручную':>16}  {'наш план':>16}")
        for key in keys:
            cells = [f"{m:g}" if m == w else f"{m:g} ({w:g})" for m, w in
                     (table["вручную"][key], table["наш план"][key])]
            print(f"{key:{width}}  {cells[0]:>16}  {cells[1]:>16}")
    seeds = min(args.seeds, 5)
    print(f"\nНехватка водителей: пустых смен на выпущенных нарядах в день, среднее по {seeds} сценариям")
    for name, table in compare_drivers(day, seeds).items():
        print(f"\n{name}")
        print(f"{'':12}  {'вручную':>10}  {'наш план':>10}")
        for kind in table["вручную"]:
            print(f"{kind:12}  {table['вручную'][kind]:>10g}  {table['наш план'][kind]:>10g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
