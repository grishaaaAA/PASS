"""Нормы труда (А2): длина смены за день и отдых на серии дней. Запуск: python -m unittest"""

import copy
import dataclasses
import json
import unittest
from datetime import date, timedelta

from naryad.core.invariants import LABOR_FILE, check_plan, check_rest, load_labor
from naryad.core.model import Day, Plan
from naryad.data.generate import generate, generate_series

from tests.test_core import SMALL, WEEKDAY, codes, greedy_plan

BASE = Day.from_dict(generate("case", WEEKDAY, seed=1, **SMALL))
SHIFT = next(iter(BASE.shifts))
DRIVER = next(iter(BASE.drivers))


def series(shifts, first="2026-10-05"):
    """Серия дней, где один водитель работает в заданное время.

    shifts - список (номер дня, начало, конец) в часах от начала того дня.
    Дни без смены - выходные водителя.
    """
    out, start = [], date.fromisoformat(first)
    by_day = {d: (s, e) for d, s, e in shifts}
    for index in range(max(by_day) + 1):
        day = copy.copy(BASE)
        day.meta = dict(BASE.meta, date=(start + timedelta(days=index)).isoformat())
        plan = Plan()
        if index in by_day:
            s, e = by_day[index]
            day.shifts = dict(BASE.shifts)
            day.shifts[SHIFT] = dataclasses.replace(BASE.shifts[SHIFT],
                                                    start=int(s * 60), end=int(e * 60))
            plan.drivers[SHIFT] = DRIVER
        out.append((day, plan))
    return out


class TestLaborFile(unittest.TestCase):

    def test_every_norm_has_source_and_status(self):
        raw = json.loads(LABOR_FILE.read_text(encoding="utf-8"))
        for name, item in raw.items():
            if name.startswith("_"):
                continue
            with self.subTest(name):
                self.assertIn("value", item)
                self.assertTrue(item["rule"] and item["status"] and item["checked"])
                self.assertIn("p424", item)  # отменённый № 424 - для истории
                if name != "max_shifts_per_driver_day":  # наше допущение, в приказе его нет
                    self.assertTrue(item["p160"])  # действующий Приказ Минтранса № 160
        self.assertIn("№ 160", raw["_about"])


class TestShiftLength(unittest.TestCase):
    """Смена 11 ч: нарушение, пока неизвестно, установил ли перевозчик смены до 12 ч."""

    def setUp(self):
        self.day = copy.copy(BASE)
        self.day.shifts = dict(BASE.shifts)
        self.plan = greedy_plan(self.day)
        shift_id = next(iter(self.plan.drivers))
        shift = self.day.shifts[shift_id]
        self.day.shifts[shift_id] = dataclasses.replace(shift, end=shift.start + 11 * 60)

    def test_eleven_hours_without_agreement(self):
        found = check_plan(self.day, self.plan)
        self.assertIn("driver_overtime", codes(found))
        self.assertTrue(any("п. 4 Приказа Минтранса № 160" in v.text for v in found))

    def test_eleven_hours_with_agreement(self):
        labor = dict(load_labor(), city_12h_allowed=True)
        self.assertNotIn("driver_overtime", codes(check_plan(self.day, self.plan, labor)))


class TestRest(unittest.TestCase):

    def test_five_two_is_fine(self):
        week = [(d, 6, 14) for d in range(5)] + [(d, 6, 14) for d in range(7, 12)]
        self.assertEqual(check_rest(series(week)), [])

    def test_seven_days_in_row(self):
        found = check_rest(series([(d, 6, 14) for d in range(7)]))
        self.assertEqual(codes(found), {"weekly_rest"})

    def test_long_shift_needs_double_rest(self):
        # 11 ч работы, через 13 ч снова на смену: нужно 22 ч минус 30 мин питания
        self.assertIn("rest_ratio", codes(check_rest(series([(0, 5, 16), (1, 5, 16)]))))

    def test_long_shifts_every_other_day_are_fine(self):
        self.assertEqual(check_rest(series([(0, 5, 16), (2, 5, 16), (4, 5, 16)])), [])

    def test_rest_shorter_than_nine_hours(self):
        # смена до 23:00, следующая с 06:00 - 7 ч отдыха
        found = check_rest(series([(0, 19, 23), (1, 6, 10)]))
        self.assertIn("daily_rest", codes(found))

    def test_reduced_rest_only_three_times(self):
        # смена 14 ч каждый день - отдых между сменами 10 ч (меньше 11, больше 9):
        # три таких сокращения допустимы, четвёртое - нарушение
        found = check_rest(series([(d, 6, 20) for d in range(5)]))
        daily = [v for v in found if v.code == "daily_rest"]
        self.assertEqual(len(daily), 1)
        three = check_rest(series([(d, 6, 20) for d in range(4)]))
        self.assertEqual([v for v in three if v.code == "daily_rest"], [])



class TestPrepTime(unittest.TestCase):
    """Подготовка и медосмотры (п. 13 Приказа № 160) - рабочее время."""

    PREP = dict(load_labor(), prep_before_min=15, prep_after_min=10)

    def test_shift_with_prep_over_limit(self):
        day = copy.copy(BASE)
        day.shifts = dict(BASE.shifts)
        plan = greedy_plan(day)
        shift_id = next(iter(plan.drivers))
        shift = day.shifts[shift_id]
        day.shifts[shift_id] = dataclasses.replace(shift, end=shift.start + 9 * 60 + 40)
        self.assertNotIn("driver_overtime", codes(check_plan(day, plan)))
        self.assertIn("driver_overtime", codes(check_plan(day, plan, self.PREP)))  # 9:40 + 0:25 > 10 ч

    def test_rest_without_prep_time(self):
        week = series([(0, 18, 22), (1, 7 + 1 / 6, 11)])  # между сменами 9 ч 10 мин
        self.assertEqual(check_rest(week), [])             # сокращённый отдых, законно
        self.assertEqual(codes(check_rest(week, self.PREP)), {"daily_rest"})  # отдых 8 ч 45 мин

    def test_solver_respects_prep_time(self):
        from naryad.solve.series import solve_series
        days = [Day.from_dict(d) for d in generate_series("park7", "2026-10-05", 5, 1, "morning")]
        plans = solve_series(days, self.PREP)
        self.assertEqual(check_rest(plans, self.PREP), [])
        self.assertFalse([v for day, plan in plans for v in check_plan(day, plan, self.PREP)])


if __name__ == "__main__":
    unittest.main()
