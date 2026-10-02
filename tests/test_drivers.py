"""Расстановка водителей (А4). Запуск: python -m unittest"""

import dataclasses
import random
import unittest

from naryad.core.invariants import check_plan, check_rest, load_labor
from naryad.core.model import Day
from naryad.data.generate import generate, generate_series
from naryad.solve.compare import compare_drivers
from naryad.solve.drivers import DriverState, History, _shift_value, rest_status, solve_drivers
from naryad.solve.series import manual_series, series_report, solve_series
from naryad.solve.vehicles import solve_vehicles

from tests.test_core import SAMPLES, WEEKDAY
from tests.test_vehicles import TINY

LABOR = load_labor()
H = 60


def week(preset="park7", days=7, seed=1):
    return [Day.from_dict(d) for d in generate_series(preset, WEEKDAY, days, seed, "morning")]


class TestRestStatus(unittest.TestCase):
    """Можно ли выйти на смену после предыдущей (п. 16, 17, 20 Приказа № 160)."""

    def state(self, end_h, length_h, in_row=1, reduced=0):
        return DriverState(last_end=end_h * H, last_length=length_h * H, in_row=in_row, reduced=reduced)

    def test_first_shift(self):
        self.assertEqual(rest_status(DriverState(), 0, LABOR), "ok")

    def test_normal_rest(self):
        self.assertEqual(rest_status(self.state(14, 8), 30 * H, LABOR), "ok")        # 16 ч

    def test_double_shift_rule(self):
        self.assertIsNone(rest_status(self.state(16, 11), 29 * H, LABOR))            # 13 ч < 21,5

    def test_reduced_rest(self):
        self.assertEqual(rest_status(self.state(14, 4), 24 * H, LABOR), "reduced")   # 10 ч
        self.assertIsNone(rest_status(self.state(14, 4, reduced=3), 24 * H, LABOR))

    def test_too_short(self):
        self.assertIsNone(rest_status(self.state(23, 4), 30 * H, LABOR))             # 7 ч

    def test_six_in_row(self):
        self.assertIsNone(rest_status(self.state(14, 8, in_row=6), 30 * H, LABOR))
        self.assertEqual(rest_status(self.state(14, 8, in_row=6), 14 * H + 45 * H, LABOR), "ok")


class TestDay(unittest.TestCase):

    def test_park7_day_valid_and_full(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        self.assertEqual(check_plan(day, plan), [])
        self.assertNotIn("no_driver", plan.unfilled.values())

    def test_driver_shortage_protects_important_routes(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        rng = random.Random(1)
        for driver_id in rng.sample(sorted(day.drivers), 400):
            day.drivers[driver_id] = dataclasses.replace(day.drivers[driver_id], schedule="sick")
        plan = solve_drivers(day, solve_vehicles(day))
        self.assertEqual(check_plan(day, plan), [])
        lost = [s for s, r in plan.unfilled.items() if r == "no_driver"]
        self.assertTrue(lost)
        share = {}
        for priority in (1, 3):
            shifts = [s for s in day.shifts.values() if day.duties[s.duty_id].type == "line"
                      and day.routes[day.duties[s.duty_id].route_id].priority == priority]
            share[priority] = sum(s.id in lost for s in shifts) / len(shifts)
        self.assertLessEqual(share[1], share[3])

    def test_best_set_of_shifts_by_brute_force(self):
        """На малых днях с нехваткой водителей ценность закрытых смен - как у полного перебора."""
        for seed in range(1, 5):
            day = Day.from_dict(generate("case", WEEKDAY, seed=seed, **TINY))
            rng = random.Random(seed)
            keep = set(rng.sample(sorted(d for d in day.drivers if day.drivers[d].schedule == "work"), 5))
            for driver_id in day.drivers:
                if driver_id not in keep:
                    day.drivers[driver_id] = dataclasses.replace(day.drivers[driver_id], schedule="day_off")
            history = History()
            for driver_id in sorted(keep)[:3]:  # трое вчера поздно закончили
                history.get(driver_id).last_end = (day_base_minutes(day) - 2 * H)
                history.get(driver_id).last_length = 8 * H
            vehicle_plan = solve_vehicles(day)
            for duty_id in sorted(vehicle_plan.vehicles)[4:]:  # малый пример: 4 наряда
                del vehicle_plan.vehicles[duty_id]
                vehicle_plan.unfilled[duty_id] = "manual"
            plan = solve_drivers(day, vehicle_plan, History(drivers={k: dataclasses.replace(v)
                                                                     for k, v in history.drivers.items()}))
            self.assertEqual(check_plan(day, plan), [])
            ours = sum(_value(day, s) for s in plan.drivers)
            self.assertAlmostEqual(ours, brute_force(day, vehicle_plan, history), places=6)


def day_base_minutes(day):
    from naryad.solve.drivers import day_base
    return day_base(day)


def _value(day, shift_id):
    shift = day.shifts[shift_id]
    return _shift_value(day, day.duties[shift.duty_id], shift.length)


def brute_force(day, vehicle_plan, history):
    """Лучшая суммарная ценность закрытых смен полным перебором назначений."""
    base = day_base_minutes(day)
    shifts = [s for d in vehicle_plan.vehicles for s in day.shifts_by_duty.get(d, [])]
    ok = {}
    for s in shifts:
        vehicle = day.vehicles[vehicle_plan.vehicles[s.duty_id]]
        ok[s.id] = [d.id for d in day.drivers.values()
                    if d.schedule == "work" and d.medical != "failed" and vehicle.cls in d.classes
                    and rest_status(history.get(d.id), base + s.start, LABOR) is not None]
    best = 0.0

    def go(i, used, value):
        nonlocal best
        if i == len(shifts):
            best = max(best, value)
            return
        rest = sum(_value(day, s.id) for s in shifts[i:])
        if value + rest <= best:
            return
        for d in ok[shifts[i].id]:
            if d not in used:
                go(i + 1, used | {d}, value + _value(day, shifts[i].id))
        go(i + 1, used, value)

    go(0, frozenset(), 0.0)
    return best


class TestSeries(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.days = week()
        cls.ours = solve_series(cls.days)

    def test_no_rest_violations(self):
        self.assertEqual(check_rest(self.ours), [])

    def test_manual_models(self):
        """Без проверки отдыха нарушения есть; сверка со вчерашним нарядом их убирает."""
        self.assertGreater(len(check_rest(manual_series(self.days, False))), 0)
        careful = check_rest(manual_series(self.days))
        self.assertFalse([v for v in careful if v.code in ("rest_ratio", "daily_rest")])

    def test_every_day_valid_and_full(self):
        report = series_report(self.ours)  # внутри - check_plan на каждый день
        filled, needed = report["смен закрыто"].split(" из ")
        self.assertEqual(filled, needed)

    def test_driver_shortage_spares_important_routes(self):
        """Водителей не хватает: смен закрыто столько же, но пустеют второстепенные маршруты."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        table = compare_drivers(day, seeds=2, scenarios={"20%": 0.2})["20%"]
        ours, manual = table["наш план"], table["вручную"]
        self.assertEqual(sum(ours.values()), sum(manual.values()))
        self.assertEqual(ours.get("важность 1", 0), 0)
        self.assertGreater(manual.get("важность 1", 0), 0)

    def test_home_drivers_kept(self):
        self.assertGreaterEqual(series_report(self.ours)["на своём закреплённом автобусе, %"], 35)


if __name__ == "__main__":
    unittest.main()
