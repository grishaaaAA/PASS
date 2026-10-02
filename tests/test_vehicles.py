"""Расстановка автобусов (А3). Запуск: python -m unittest"""

import dataclasses
import itertools
import time
import unittest
from collections import defaultdict

from naryad.core.invariants import check_plan
from naryad.core.model import Day, Plan
from naryad.data.generate import generate
from naryad.solve.baseline import baseline_vehicles
from naryad.solve.compare import compare, report, with_shortage, worst_growth
from naryad.solve.vehicles import _groups, allowed_classes, drop_costs, solve_vehicles, total_cost

from tests.test_core import SAMPLES, WEEKDAY

TINY = dict(park_count=1, release_per_park=14, routes_total=3,
            class_mix={"medium": 5, "big": 5, "extra_big": 4})


def brute_force_cost(day: Day) -> float:
    """Лучшая цена пропусков полным перебором: сколько нарядов выпадает в каждой группе.

    Выполнимость - по каждому парку: лимит выпуска и условие Холла для
    классов (нарядам, которым годятся только классы из набора S, хватает
    исправных автобусов классов S).
    """
    groups = list(_groups(day).values())
    supply = defaultdict(int)
    for v in day.vehicles.values():
        if v.condition == "ok":
            supply[(v.park_id, v.cls)] += 1
    classes = sorted({c for g in groups for c in allowed_classes(day, g[0])})
    subsets = [set(c) for r in range(1, len(classes) + 1) for c in itertools.combinations(classes, r)]
    costs = [[c for _, c in drop_costs(day, g)] for g in groups]
    best = float("inf")
    for missing in itertools.product(*(range(len(g) + 1) for g in groups)):
        kept = defaultdict(int)  # (парк, допустимые классы) -> нарядов
        for g, m in zip(groups, missing):
            kept[(g[0].park_id, frozenset(allowed_classes(day, g[0])))] += len(g) - m
        ok = True
        for park in day.parks.values():
            here = {k: n for (p, k), n in kept.items() if p == park.id}
            limit = 0 if park.state == "down" else park.release(day.day_type)
            ok = ok and sum(here.values()) <= limit and all(
                sum(n for k, n in here.items() if k <= sub) <= sum(supply[(park.id, c)] for c in sub)
                for sub in subsets)
        if ok:
            best = min(best, sum(sum(c[:m]) for c, m in zip(costs, missing)))
    return best


def multiclass_day(seed: int, share: float) -> Day:
    """Малый день, где маршруты допускают несколько классов, а лимит выпуска меньше нарядов."""
    data = generate("case", WEEKDAY, seed=seed, **TINY)
    extra = {"medium": "big", "big": "extra_big", "extra_big": "big"}
    for i, route in enumerate(sorted(data["routes"], key=lambda r: r["id"])):
        if i % 2 == seed % 2:
            route["allowed_classes"].insert(0, extra[route["allowed_classes"][0]])  # чужой класс первым
    data["parks"][0]["release_weekday"] -= seed % 3 + 1
    return with_shortage(Day.from_dict(data), share, seed)


class TestVehicles(unittest.TestCase):

    def test_normal_day_fills_everything(self):
        for name in ("park7_weekday.json", "park7_weekend.json"):
            with self.subTest(name):
                day = Day.load(SAMPLES / name)
                plan = solve_vehicles(day)
                self.assertEqual(check_plan(day, plan, drivers=False), [])
                self.assertEqual(plan.unfilled, {})

    def test_whole_city_fast_and_valid(self):
        day = Day.from_dict(generate("case", WEEKDAY, seed=1))
        start = time.perf_counter()
        plan = solve_vehicles(day)
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertEqual(check_plan(day, plan, drivers=False), [])

    def test_optimal_by_brute_force(self):
        """На малых днях с нехваткой цена совпадает с полным перебором."""
        checked = 0
        for seed in range(1, 6):
            base = Day.from_dict(generate("case", WEEKDAY, seed=seed, **TINY))
            for share in (0.2, 0.35, 0.5):
                day = with_shortage(base, share, seed)
                plan = solve_vehicles(day)
                self.assertEqual(check_plan(day, plan, drivers=False), [])
                self.assertAlmostEqual(total_cost(day, plan), brute_force_cost(day), places=6)
                checked += 1
        self.assertEqual(checked, 15)

    def test_optimal_with_several_classes_and_release_limit(self):
        """Маршруты на несколько классов и действующий лимит выпуска: тоже точный оптимум."""
        reasons = set()
        for seed in range(1, 6):
            for share in (0.2, 0.4):
                day = multiclass_day(seed, share)
                plan = solve_vehicles(day)
                self.assertEqual(check_plan(day, plan, drivers=False), [])
                self.assertAlmostEqual(total_cost(day, plan), brute_force_cost(day), places=6)
                reasons |= set(plan.unfilled.values())
        self.assertIn("release_limit", reasons)  # лимит выпуска действительно ограничивал

    def test_release_limit_binds(self):
        """Лимит выпуска меньше нарядов: оба способа его соблюдают и называют причину."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        park = next(iter(day.parks.values()))
        day.parks[park.id] = dataclasses.replace(park, release_weekday=300, release_weekend=300)
        for solver in (solve_vehicles, baseline_vehicles):
            with self.subTest(solver.__name__):
                plan = solver(day)
                self.assertEqual(check_plan(day, plan, drivers=False), [])
                self.assertEqual(len(plan.vehicles), 300)
                self.assertIn("release_limit", plan.unfilled.values())

    def test_reserve_goes_first(self):
        day = with_shortage(Day.load(SAMPLES / "park7_weekday.json"), 0.06, 1)
        plan = solve_vehicles(day)
        lost = [day.duties[d] for d in plan.unfilled]
        self.assertTrue(lost)
        self.assertTrue(all(d.type == "reserve" for d in lost))

    def test_never_worse_than_manual(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        for share in (0.05, 0.1, 0.15, 0.25):
            for seed in (1, 2, 3):
                scenario = with_shortage(day, share, seed)
                ours, manual = solve_vehicles(scenario), baseline_vehicles(scenario)
                self.assertEqual(check_plan(scenario, manual, drivers=False), [])
                self.assertLessEqual(total_cost(scenario, ours), total_cost(scenario, manual) + 1e-9)

    def test_own_vehicles_on_own_routes(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_vehicles(day)
        own = sum(day.duties[d].type == "line"
                  and day.vehicles[v].home_route_id == day.duties[d].route_id
                  for d, v in plan.vehicles.items())
        homes = sum(v.home_route_id is not None and v.condition == "ok"
                    for v in day.vehicles.values())
        self.assertEqual(own, homes)  # все исправные «свои» автобусы стоят на своих маршрутах

    def test_same_input_same_plan(self):
        day = with_shortage(Day.load(SAMPLES / "park7_weekday.json"), 0.15, 7)
        self.assertEqual(solve_vehicles(day).vehicles, solve_vehicles(day).vehicles)

    def test_comparison_numbers(self):
        """Цифры для демо: при нехватке 15% важные маршруты теряют меньше, линия закрыта так же."""
        table = compare(Day.load(SAMPLES / "park7_weekday.json"), seeds=3)["нехватка 15%"]
        ours, manual = table["наш план"], table["вручную"]
        important = "потеряно нарядов важных маршрутов"
        self.assertLess(ours[important][1], manual[important][0])  # наш худший лучше их среднего
        self.assertEqual(ours["закрыто нарядов на линии"], manual["закрыто нарядов на линии"])
        self.assertLess(ours["цена пропусков"][0], manual["цена пропусков"][0])
        jumps = "маршрутов с ростом интервала > 25%"
        self.assertLess(ours[jumps][0], manual[jumps][0])  # куб в цене пропуска размазывает нехватку

    def test_report_counts_directly(self):
        day = with_shortage(Day.load(SAMPLES / "park7_weekday.json"), 0.15, 2, by_class=False)
        for plan in (solve_vehicles(day), baseline_vehicles(day)):
            lines = [d for d in day.duties.values() if d.type == "line" and d.day_type == day.day_type]
            row = report(day, plan)
            self.assertEqual(row["закрыто нарядов на линии"], sum(d.id in plan.vehicles for d in lines))
            self.assertEqual(row["потеряно нарядов важных маршрутов"],
                             sum(d.id not in plan.vehicles and day.routes[d.route_id].priority == 1
                                 for d in lines))

    def test_interval_growth_by_moment(self):
        """Рост интервала - по автобусам в каждый момент, а не по нарядам за день."""
        duty = Day.load(SAMPLES / "park7_weekday.json").duties["P07-R01-WD01"]
        a, b, c = (dataclasses.replace(duty, id=i, start=s, end=e)
                   for i, s, e in (("a", 0, 100), ("b", 0, 300), ("c", 200, 300)))
        self.assertEqual(worst_growth(Plan(vehicles={"a": "1", "b": "2", "c": "3"}), [a, b, c]), 1.0)
        self.assertEqual(worst_growth(Plan(vehicles={"a": "1", "b": "2"}), [a, b, c]), 2.0)  # по дню 1,5
        self.assertEqual(worst_growth(Plan(vehicles={"b": "2"}), [a, b, c]), 2.0)
        for running in ({"a": "1", "c": "3"}, {"a": "1"}):  # с 100-й по 200-ю минуту автобусов нет
            self.assertEqual(worst_growth(Plan(vehicles=running), [a, b, c]), float("inf"))


if __name__ == "__main__":
    unittest.main()
