"""Предел перевозчика за месяц: 180 ч рабочего времени на водителя. Запуск: python -m unittest

Со слов директора парка №6 (13.10.2026): водитель работает до 12 ч в день,
но не больше 180 ч в месяц. Рабочее время - смены вместе с подготовкой и
медосмотрами (п. 13 Приказа № 160), месяц - календарный, по дате дня.
"""

from __future__ import annotations

import unittest

from naryad.core.invariants import check_rest, load_labor
from naryad.core.model import Day
from naryad.explain import why_driver, why_unfilled
from naryad.ops.replan import Segment, check_state, free_drivers
from naryad.solve.drivers import DriverState, History, remember, solve_drivers
from naryad.solve.vehicles import solve_vehicles

from tests.test_core import SAMPLES
from tests.test_labor import series
from tests.test_ops import MORNING, important_running, morning_state

SAMPLE = SAMPLES / "park7_weekday.json"   # 05.10.2026
LIMIT = load_labor()["max_month_work_min"]


def worked(ids, minutes: int, month: str = "2026-10") -> History:
    """Память, где у водителей ids в месяце month уже отработано minutes."""
    history = History()
    for driver_id in ids:
        history.get(driver_id).month, history.get(driver_id).month_minutes = month, minutes
    return history


class TestNorm(unittest.TestCase):

    def test_limit_and_twelve_hours(self):
        labor = load_labor()
        self.assertEqual(LIMIT, 180 * 60)
        self.assertTrue(labor["city_12h_allowed"])

    def test_memory_counts_prep_time(self):
        labor = dict(load_labor(), prep_before_min=20, prep_after_min=10)
        state = DriverState()
        remember(state, 0, 8 * 60, "2026-10", labor)
        self.assertEqual(state.month_minutes, 8 * 60 + 30)


class TestSeries(unittest.TestCase):

    @staticmethod
    def month_hours(days, first):
        return [v for v in check_rest(series(days, first)) if v.code == "month_hours"]

    def test_over_the_limit_is_caught_once(self):
        days = [(d, 6, 16) for d in range(31) if d % 6 < 4]   # 4 через 2 по 10 ч: 21 смена, 210 ч
        found = self.month_hours(days, "2026-10-01")
        self.assertEqual(len(found), 1)
        self.assertIn("предел перевозчика 180 ч", found[0].text)
        self.assertIn("190 ч 00 мин", found[0].text)   # первая смена сверх предела - девятнадцатая

    def test_exactly_the_limit_is_fine(self):
        days = [(d, 6, 16) for d in range(27) if d % 6 < 4][:18]   # 18 смен по 10 ч = 180 ч
        self.assertEqual(self.month_hours(days, "2026-10-01"), [])

    def test_month_starts_from_zero(self):
        days = [(d, 6, 16) for d in range(31) if d % 6 < 4]   # те же 210 ч, но с 20.10 по 19.11
        self.assertEqual(self.month_hours(days, "2026-10-20"), [])


class TestSolver(unittest.TestCase):

    def setUp(self):
        self.day = Day.load(SAMPLE)
        plan = solve_drivers(self.day, solve_vehicles(self.day))
        self.ids = set(sorted(set(plan.drivers.values()))[:30])

    def test_no_shift_beyond_the_limit(self):
        again = solve_drivers(self.day, solve_vehicles(self.day), worked(self.ids, LIMIT - 60))
        self.assertEqual(set(again.drivers.values()) & self.ids, set())

    def test_last_month_does_not_count(self):
        again = solve_drivers(self.day, solve_vehicles(self.day), worked(self.ids, LIMIT - 60, "2026-09"))
        self.assertTrue(set(again.drivers.values()) & self.ids)

    def test_explained(self):
        everyone = worked(self.day.drivers, LIMIT - 60)
        plan = solve_drivers(self.day, solve_vehicles(self.day), worked(self.day.drivers, LIMIT - 60))
        self.assertFalse(plan.drivers)
        shift_id = next(s for s in sorted(plan.unfilled) if s in self.day.shifts)
        text = " ".join(why_unfilled(self.day, plan, shift_id, everyone)["reasons"])
        self.assertIn("выработали месячный предел 180 ч", text)

    def test_hours_so_far_are_shown_with_the_limit(self):
        plan = solve_drivers(self.day, solve_vehicles(self.day), worked(self.day.drivers, 100 * 60))
        shift_id = sorted(plan.drivers)[0]
        reasons = why_driver(self.day, plan, shift_id, worked(self.day.drivers, 100 * 60))["reasons"]
        self.assertIn("В этом месяце отработал 100 ч 00 мин из 180 ч", reasons)


class TestWithinDay(unittest.TestCase):

    def test_replacement_and_manual_placement(self):
        state = morning_state()
        duty = important_running(state)
        labor = load_labor()
        found = free_drivers(state, duty, MORNING, MORNING + 4 * 60, labor)
        tired = found[0]
        state.history = worked([tired], LIMIT - 60)
        self.assertNotIn(tired, free_drivers(state, duty, MORNING, MORNING + 4 * 60, labor))
        self.assertIn(tired, free_drivers(state, duty, MORNING, MORNING + 60, labor))   # час ещё есть
        shift_id = next(s for s in sorted(state.drivers) if state.day.shifts[s].duty_id == duty.id)
        shift = state.day.shifts[shift_id]
        state.drivers[shift_id] = [Segment(shift.start, shift.end, tired)]
        said = [v.text for v in check_state(state) if v.code == "month_hours"]
        self.assertEqual(len(said), 1, said)
        self.assertIn("предел перевозчика 180 ч", said[0])


if __name__ == "__main__":
    unittest.main()
